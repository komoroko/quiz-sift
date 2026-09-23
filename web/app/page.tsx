"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api, type DeckSummary } from "@/lib/api";

export default function DecksPage() {
  const [decks, setDecks] = useState<DeckSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.decks().then(setDecks, (e: Error) => setError(e.message));
  }, []);

  if (error) return <p role="alert">{error}</p>;
  if (!decks) return <p>読み込み中…</p>;
  return (
    <>
      <h1>デッキ</h1>
      {decks.length === 0 ? (
        <p>data/ にデッキがありません。</p>
      ) : (
        <ul>
          {decks.map((deck) => (
            <li key={deck.deck_id}>
              <Link href={`/decks/${encodeURIComponent(deck.deck_id)}`}>{deck.title}</Link> ({deck.language})
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
