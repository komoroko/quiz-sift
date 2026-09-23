"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { api, type Graded, type PresentedQuestion } from "@/lib/api";

// 出題・採点・記録はすべて API 側。この画面は API が返した選択肢の並びを表示し、
// 選ばれた option_id を送るだけで、回答をブラウザ内に保存しない。
export default function SessionPage() {
  const { deck, session } = useParams<{ deck: string; session: string }>();
  const [remaining, setRemaining] = useState<string[] | null>(null);
  const [question, setQuestion] = useState<PresentedQuestion | null>(null);
  const [graded, setGraded] = useState<Graded | null>(null);
  const [error, setError] = useState<string | null>(null);

  const next = useCallback(async () => {
    try {
      const detail = await api.session(deck, session);
      const answered = new Set(detail.answered);
      const rest = detail.question_ids.filter((id) => !answered.has(id));
      setRemaining(rest);
      setGraded(null);
      setQuestion(rest.length > 0 ? await api.present(deck, session, rest[0]) : null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [deck, session]);

  useEffect(() => {
    void next();
  }, [next]);

  async function choose(optionId: string) {
    if (!question || graded) return;
    try {
      setGraded(await api.submit(deck, session, question.question_id, optionId));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (error) return <p role="alert">{error}</p>;
  if (remaining === null) return <p>読み込み中…</p>;
  const back = <Link href={`/decks/${encodeURIComponent(deck)}`}>デッキへ戻る</Link>;
  if (!question) return <><p>このセッションの問題はすべて回答済みです。</p>{back}</>;
  return (
    <>
      <p>残り {remaining.length} 問 · セッション {session}</p>
      <h2>{question.stem}</h2>
      <ol type="A">
        {question.options.map((option) => {
          const mark = graded && option.option_id === graded.correct_option_id ? " ✓" : "";
          return (
            <li key={option.option_id}>
              <button disabled={graded !== null} onClick={() => choose(option.option_id)}>
                {option.text}
              </button>
              {mark}
            </li>
          );
        })}
      </ol>
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
