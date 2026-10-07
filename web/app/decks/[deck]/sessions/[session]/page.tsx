"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, type Graded, type PresentedQuestion } from "@/lib/api";

// 出題・採点・記録はすべて API 側。この画面は API が返した選択肢の並びを表示し、
// 選ばれた option_id の集合を送るだけで、回答をブラウザ内に保存しない。
// 正解が1件の問題は選択肢を1クリックで送る。select_count が2以上の問題はチェックボックスで選び、
// 選択がちょうど select_count 件のときだけ送信できる(D-199)。
export default function SessionPage() {
  const { deck, session } = useParams<{ deck: string; session: string }>();
  const [remaining, setRemaining] = useState<string[] | null>(null);
  const [question, setQuestion] = useState<PresentedQuestion | null>(null);
  const [graded, setGraded] = useState<Graded | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  // 応答を待つ間に2度目の送信を出さない。2度目はサーバが回答済みとして拒み、1度目の採点表示を潰す。
  // 再描画前に続けて届いたクリックは古い state を見るので、判定は ref で行い、state はボタンの無効化に使う。
  const sending = useRef(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const next = useCallback(async () => {
    try {
      const detail = await api.session(deck, session);
      const answered = new Set(detail.answered);
      const rest = detail.question_ids.filter((id) => !answered.has(id));
      setRemaining(rest);
      setGraded(null);
      setSelected([]);
      setQuestion(rest.length > 0 ? await api.present(deck, session, rest[0]) : null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [deck, session]);

  useEffect(() => {
    void next();
  }, [next]);

  async function submit(chosen: string[]) {
    if (!question || graded || sending.current) return;
    sending.current = true;
    setSubmitting(true);
    try {
      setGraded(await api.submit(deck, session, question.question_id, chosen));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      sending.current = false;
      setSubmitting(false);
    }
  }

  function toggle(optionId: string) {
    setSelected((current) =>
      current.includes(optionId) ? current.filter((id) => id !== optionId) : [...current, optionId],
    );
  }

  if (error) return <p role="alert">{error}</p>;
  if (remaining === null) return <p>読み込み中…</p>;
  const back = <Link href={`/decks/${encodeURIComponent(deck)}`}>デッキへ戻る</Link>;
  if (!question) return <><p>このセッションの問題はすべて回答済みです。</p>{back}</>;
  const multiple = question.select_count > 1;
  return (
    <>
      <p>残り {remaining.length} 問 · セッション {session}</p>
      <h2>{question.stem}</h2>
      {multiple ? <p>{question.select_count} 個選択</p> : null}
      <ol type="A">
        {question.options.map((option) => {
          const mark = graded && graded.correct_option_ids.includes(option.option_id) ? " ✓" : "";
          return (
            <li key={option.option_id}>
              {multiple ? (
                <label>
                  <input
                    type="checkbox"
                    disabled={graded !== null || submitting}
                    checked={selected.includes(option.option_id)}
                    onChange={() => toggle(option.option_id)}
                  />
                  {option.text}
                </label>
              ) : (
                <button disabled={graded !== null || submitting} onClick={() => void submit([option.option_id])}>
                  {option.text}
                </button>
              )}
              {mark}
            </li>
          );
        })}
      </ol>
      {multiple && !graded ? (
        <button
          disabled={submitting || selected.length !== question.select_count}
          onClick={() => void submit(selected)}>
          回答する
        </button>
      ) : null}
      {graded ? (
        <>
          <p role="status">{graded.correct ? "正解" : "不正解"}</p>
          {graded.explanation ? <p>{graded.explanation}</p> : null}
          <button onClick={() => void next()}>次へ</button>
        </>
      ) : null}
      <p>{back}</p>
    </>
  );
}
