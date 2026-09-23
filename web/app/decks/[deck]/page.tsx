"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { api, type DeckDetail, type SessionSummary } from "@/lib/api";

export default function DeckPage() {
  const { deck } = useParams<{ deck: string }>();
  const router = useRouter();
  const [detail, setDetail] = useState<DeckDetail | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [pre, setPre] = useState("");
  const [post, setPost] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.deck(deck), api.sessions(deck)]).then(([d, s]) => {
      setDetail(d);
      setSessions(s);
    }, (e: Error) => setError(e.message));
  }, [deck]);

  async function start() {
    try {
      const { session_id } = await api.startSession(deck);
      router.push(`/decks/${encodeURIComponent(deck)}/sessions/${encodeURIComponent(session_id)}`);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (error) return <p role="alert">{error}</p>;
  if (!detail) return <p>読み込み中…</p>;
  const reportHref =
    `/decks/${encodeURIComponent(deck)}/report?pre=${encodeURIComponent(pre)}&post=${encodeURIComponent(post)}`;
  return (
    <>
      <h1>{detail.title}</h1>
      <p>{detail.question_ids.length} 問</p>
      <button onClick={start}>テストを始める</button>

      <h2>実施済みのセッション</h2>
      {sessions.length === 0 ? (
        <p>まだありません。</p>
      ) : (
        <ul>
          {sessions.map((s) => (
            <li key={s.session_id}>
              <Link href={`/decks/${encodeURIComponent(deck)}/sessions/${encodeURIComponent(s.session_id)}`}>
                {s.session_id}
              </Link>{" "}
              ({s.answered} 問回答)
            </li>
          ))}
        </ul>
      )}

      <h2>事前・事後の比較</h2>
      <label>
        事前{" "}
        <select value={pre} onChange={(e) => setPre(e.target.value)}>
          <option value="">選択</option>
          {sessions.map((s) => (
            <option key={s.session_id} value={s.session_id}>{s.session_id}</option>
          ))}
        </select>
      </label>{" "}
      <label>
        事後{" "}
        <select value={post} onChange={(e) => setPost(e.target.value)}>
          <option value="">選択</option>
          {sessions.map((s) => (
            <option key={s.session_id} value={s.session_id}>{s.session_id}</option>
          ))}
        </select>
      </label>{" "}
      {pre && post ? <Link href={reportHref}>レポートを見る</Link> : null}
    </>
  );
}
