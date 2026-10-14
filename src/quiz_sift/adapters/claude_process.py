"""`claude` の子プロセス境界(ADR-014 / ADR-017)。

`SubprocessRunner` は実 spawn だけを担い、ゲートでは fake に差し替わる。`Workspace` はファイルシステム
しか触らないので、ゲートでも差し替えず `tmp_path` を根にして実物を走らせる(AR-72)。

import・`SubprocessRunner()`・`Workspace()` の構築はファイルシステムに何もしない(D-115)。起動前検査は
`default_preflight` が呼ばれたときにだけ走る。
"""

import contextlib
import fcntl
import functools
import json
import os
import shutil
import signal
import stat
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import NoReturn

from quiz_sift.adapters.llm_prompts import model_env
from quiz_sift.ports.process import CommandResult, CommandSpec, Outcome

CHILD_ENV_KEYS = frozenset({"HOME", "PATH", "LANG", "TZ"})
"""子プロセスへ渡す環境の基本の許可集合(NFR-8 (b))。親環境は継承しない。メソッドごとの追加は
`llm_prompts.CHILD_ENV`(NFR-8 (b2))。"""

MINIMAL_HOME_NAME = ".quiz-sift-claude-home"
"""人間が `HOME=~/.quiz-sift-claude-home claude /login` で用意する最小ホーム(ADR-014)。"""

ALLOWED_SETTINGS_KEYS = frozenset({"theme"})
"""最小ホームの `.claude/settings.json` に許すトップレベルのキー(ADR-017、D-122)。"""

KILL_GRACE_SEC = 5
"""プロセスグループに SIGKILL を送ってから pipe の終端を待つ秒数(D-186)。"""

DEFERRED_SIGNALS = frozenset({signal.SIGHUP, signal.SIGTERM, signal.SIGALRM, signal.SIGINT})
"""spawn の間だけ遅らせるシグナル。どれもハンドラが例外を送出しうる(12 時間の上限・D-221・Ctrl-C)。"""

FALLBACK_LANG = "C.UTF-8"
FALLBACK_TZ = "Asia/Tokyo"


class PreflightError(SystemExit):
    """起動前検査の不合格。メッセージを stderr に出して非 0 で終了する。"""


class SubprocessRunner:
    """`CommandRunner` の実装。env は `spec.env` だけを渡し、その変数名は許可集合と一致しなければならない。

    許可集合は基本の4つに、argv の `--model` を使うメソッドの追加の環境(`llm_prompts.model_env`)の鍵を足したもので、
    追加の変数は値も表と一致しなければならない(NFR-8 (b2))。

    子は新しいセッション(プロセスグループ)で起動し、止めるときはグループごと SIGKILL する(ADR-023)。
    子だけを kill すると、pipe を継承した孫が書き込み側を開いたまま残り、`communicate()` が戻らない。
    """

    def run(self, spec: CommandSpec) -> CommandResult:
        extra = model_env(_model(spec.argv))
        allowed = CHILD_ENV_KEYS | extra.keys()
        if set(spec.env) != allowed or any(spec.env[key] != value for key, value in extra.items()):
            raise ValueError(
                f"子プロセスの環境は {sorted(allowed)} と追加の値 {extra} に一致しない: {sorted(spec.env)}"
            )
        # Popen の内側(fork の後)や、Popen が戻ってから下の try に入るまでに例外が来ると、起動済みの子を
        # killpg する経路が無い。そこまでは DEFERRED_SIGNALS を遅らせ、届いていた分は下の try の中で処理する。
        # 子はマスクを継承するので、exec の前に元のマスクへ戻す。マスクはスレッドごとなので、この遅延は
        # run を呼んだスレッドだけがシグナルを受けられる構成(CLI の主スレッド)で効く。
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, DEFERRED_SIGNALS)
        try:
            proc = subprocess.Popen(
                spec.argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=spec.cwd,
                env=dict(spec.env),
                start_new_session=True,
                preexec_fn=functools.partial(signal.pthread_sigmask, signal.SIG_SETMASK, mask),
            )
        except OSError as exc:
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)
            return CommandResult(Outcome.SPAWN, None, b"", str(exc).encode())
        except BaseException:
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)
            raise
        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)
            stdout, stderr = proc.communicate(spec.stdin.encode(), timeout=spec.timeout_sec)
        except subprocess.TimeoutExpired:
            # 後始末の最初の操作として、Python の関数を挟まずに killpg する(下の節の注記と同じ理由)。
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # グループの全員がもう終わっている。止める相手がいないだけ
            stdout, stderr = _stop_group(proc)
            return CommandResult(Outcome.TIMEOUT, None, stdout, stderr)
        except BaseException:
            # signal.alarm や親の SIGHUP/SIGTERM のハンドラが送出する SystemExit の派生などは、子のグループを
            # 残さずにそのまま通す(AR-149、D-221)。後始末に入った直後に次のシグナルが届くと、そのハンドラは
            # 次に呼ばれる Python の関数の入口で走り、例外が差し替わって後始末が飛ぶ。そのため最初の操作は
            # 組み込み関数の os.killpg を直接呼ぶ(組み込み関数は、保留中のハンドラを呼び出しの後でしか走らせない)。
            # SIGTERM は挟まない(D-209)。子は wait されるまでゾンビとして残るので、グループ ID(= 子の pid)が
            # 他へ再利用されることはない。
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # グループの全員がもう終わっている。止める相手がいないだけ
            _close_pipes(proc)
            proc.wait()
            raise
        return CommandResult(Outcome.EXITED, proc.returncode, stdout, stderr)


def _model(argv: tuple[str, ...]) -> str:
    """argv の `--model` の値。無ければ空文字(追加の環境を許さない)。"""
    return argv[argv.index("--model") + 1] if "--model" in argv[:-1] else ""


def _close_pipes(proc: subprocess.Popen[bytes]) -> None:
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        if pipe is not None:
            pipe.close()


def _stop_group(proc: subprocess.Popen[bytes]) -> tuple[bytes, bytes]:
    """SIGKILL を送ったタイムアウトした子のグループの終わりを待ち、それまでの出力を返す。

    `communicate` は読んだ分を内部に溜めていて、続けて呼ぶとそこから先を読み足した全体を返す。
    新しいセッションに抜けた孫が pipe を持ち続けると `KILL_GRACE_SEC` でも終端が来ないので、そのときは
    溜まっていた分だけを持って自分で pipe を閉じる(孫の終了は待たない)。
    """
    try:
        return proc.communicate(timeout=KILL_GRACE_SEC)
    except subprocess.TimeoutExpired as late:
        _close_pipes(proc)
        proc.wait()
        return late.stdout or b"", late.stderr or b""


class Workspace:
    """最小ホーム(`home_root`)と、呼び出しごとの空の作業ディレクトリ(`cwd_root` の下)。

    `home_root` の既定は `~/.quiz-sift-claude-home`、`cwd_root` の既定は `tempfile` の既定の置き場。
    どちらも構築時には解決するだけで、ファイルシステムには触らない。
    """

    def __init__(self, home_root: Path | None = None, cwd_root: Path | None = None) -> None:
        self.home_root = home_root if home_root is not None else Path.home() / MINIMAL_HOME_NAME
        self.cwd_root = cwd_root

    def child_env(self) -> dict[str, str]:
        """子プロセスへ渡す環境。`HOME` は最小ホーム、`PATH` は親の値をそのまま写す。"""
        return {
            "HOME": str(self.home_root),
            "PATH": os.environ["PATH"],
            "LANG": os.environ.get("LANG") or FALLBACK_LANG,
            "TZ": os.environ.get("TZ") or FALLBACK_TZ,
        }

    @contextlib.contextmanager
    def new_cwd(self) -> Iterator[Path]:
        """その呼び出しのためだけの空ディレクトリ。抜けるときに中身ごと消す。"""
        cwd = Path(tempfile.mkdtemp(prefix="quiz-sift-cwd-", dir=self.cwd_root))
        try:
            yield cwd
        finally:
            shutil.rmtree(cwd)

    def preflight(self) -> None:
        """起動前検査(NFR-8 (b)、ADR-017)。`claude` の解決 → ロック → 中身検査の順に行う。

        読むだけで、mkdir・書き込み・修復はしない。合格したときのロックはプロセスが終わるまで持ち続ける。
        """
        if shutil.which("claude", path=self.child_env()["PATH"]) is None:
            raise PreflightError("claude が子プロセスの PATH に見つからない(Claude Code CLI を入れてログインする)")
        try:
            fd = os.open(self.home_root, os.O_RDONLY | os.O_DIRECTORY)
        except OSError as exc:
            raise PreflightError(f"最小ホーム {self.home_root} をディレクトリとして開けない: {exc}") from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise PreflightError(f"最小ホーム {self.home_root} は別のプロセスが使用中(ロックを取れない)") from exc
        try:
            self._inspect()
        except BaseException:
            os.close(fd)
            raise

    def _inspect(self) -> None:
        claude = self.home_root / ".claude"
        claude_md = claude / "CLAUDE.md"
        if os.path.lexists(claude_md):
            self._reject("i", claude_md, "CLAUDE.md を置いてはならない")
        rules = claude / "rules"
        if os.path.lexists(rules):
            if not stat.S_ISDIR(os.lstat(rules).st_mode):
                self._reject("ii", rules, "rules はディレクトリでなければならない")
            for entry in _non_directories(rules):
                self._reject("ii", entry, "rules の配下にファイルを置いてはならない")
        settings = claude / "settings.json"
        if os.path.lexists(settings):
            self._check_settings(settings)
        credentials = claude / ".credentials.json"
        if not os.path.lexists(credentials):
            self._reject("iv", credentials, "資格情報が無い(HOME=<最小ホーム> claude /login を1回走らせる)")

    def _check_settings(self, settings: Path) -> None:
        try:
            parsed = json.loads(settings.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._reject("iii", settings, f"UTF-8 の JSON として読めない: {exc}")
        if not isinstance(parsed, dict):
            self._reject("iii", settings, "トップレベルが object でない")
        extra = set(parsed) - ALLOWED_SETTINGS_KEYS
        if extra:
            self._reject("iii", settings, f"許可されないキー {sorted(extra)}(許可は {sorted(ALLOWED_SETTINGS_KEYS)})")

    def _reject(self, rule: str, path: Path, reason: str) -> NoReturn:
        relative = path.relative_to(self.home_root).as_posix()
        raise PreflightError(f"最小ホームの検査に不合格(規則 {rule}): {relative}: {reason}")


def _non_directories(root: Path) -> list[Path]:
    """`root` の配下を symlink をたどらずに見て、ディレクトリでない項目(symlink を含む)を返す。"""
    found: list[Path] = []
    for current, dirnames, filenames in os.walk(root, onerror=_raise):
        base = Path(current)
        found.extend(base / name for name in dirnames if (base / name).is_symlink())
        found.extend(base / name for name in filenames)
    return sorted(found)


def _raise(exc: OSError) -> NoReturn:
    """`os.walk` が読めなかったディレクトリを黙って飛ばさないようにする。"""
    raise exc


def default_preflight() -> None:
    """製品の既定の起動前検査。呼ばれたときに `Workspace` を構築して `preflight()` を走らせる。"""
    Workspace().preflight()
