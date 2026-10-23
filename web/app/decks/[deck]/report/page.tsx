"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { api, type PrePost, type Report } from "@/lib/api";

const percent = (ratio: number | null) => (ratio === null ? "—" : `${(ratio * 100).toFixed(1)}%`);
const points = (delta: number | null) =>
  delta === null ? "—" : `${delta >= 0 ? "+" : ""}${(delta * 100).toFixed(1)}pt`;

function Row({ label, pair }: { label: string; pair: PrePost }) {
  return (
    <tr>
      <td>{label}</td>
      <td>{percent(pair.pre.ratio)} ({pair.pre.correct}/{pair.pre.answered})</td>
      <td>{percent(pair.post.ratio)} ({pair.post.correct}/{pair.post.answered})</td>
      <td>{points(pair.delta)}</td>
    </tr>
  );
}

function ReportView() {
  const { deck } = useParams<{ deck: string }>();
  const query = useSearchParams();
  const pre = query.get("pre") ?? "";
  const post = query.get("post") ?? "";
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.report(deck, pre, post).then(setReport, (e: Error) => setError(e.message));
  }, [deck, pre, post]);

  if (error) return <p role="alert">{error}</p>;
  if (!report) return <p>読み込み中…</p>;
  return (
    <>
      <h1>事前・事後レポート</h1>
      <p>事前 {report.pre_session} / 事後 {report.post_session}</p>
      <table>
        <thead>
          <tr><th>区分</th><th>事前</th><th>事後</th><th>差</th></tr>
        </thead>
        <tbody>
          <Row label="全体" pair={report.overall} />
          {Object.entries(report.by_studied).map(([flag, pair]) => (
            <Row key={flag} label={flag} pair={pair} />
          ))}
        </tbody>
      </table>
      <p>{report.limits}</p>
      <Link href={`/decks/${encodeURIComponent(deck)}`}>デッキへ戻る</Link>
    </>
  );
}

export default function ReportPage() {
  return (
    <Suspense fallback={<p>読み込み中…</p>}>
      <ReportView />
    </Suspense>
  );
}
