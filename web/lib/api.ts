// ローカル API(src/quiz_sift/server/)の型付きの口。R-12 / ADR-006。
//
// UI は表示と OptionId の送信だけを受け持つ。採点・シャッフル・集計はすべて API 側にあり、
// 回答の永続化も API が学習記録ストアへ書く1経路だけ。ブラウザ内ストレージには何も残さない。
// 呼び先は同一オリジンの /api で、next.config.mjs の rewrites がローカル API へ転送する。

export type DeckSummary = { deck_id: string; title: string; language: string };
export type DeckDetail = { deck_id: string; title: string; question_ids: string[] };
export type SessionSummary = { session_id: string; answered: number };
export type SessionDetail = { session_id: string; question_ids: string[]; answered: string[] };
export type PresentedQuestion = {
  question_id: string;
  stem: string;
  options: { option_id: string; text: string }[];
};
export type Graded = { correct: boolean; correct_option_id: string; explanation: string | null };
export type Rate = { correct: number; answered: number; ratio: number | null };
export type PrePost = { pre: Rate; post: Rate; delta: number | null };
export type Report = {
  deck_id: string;
  pre_session: string;
  post_session: string;
  overall: PrePost;
  by_studied: Record<string, PrePost>;
  limits: string;
  markdown: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { cache: "no-store", ...init });
  const body = await response.json();
  if (!response.ok) {
    throw new Error(typeof body?.error === "string" ? body.error : `HTTP ${response.status}`);
  }
  return body as T;
}

const seg = encodeURIComponent;

export const api = {
  decks: () => request<DeckSummary[]>("/api/decks"),
  deck: (deck: string) => request<DeckDetail>(`/api/decks/${seg(deck)}`),
  sessions: (deck: string) => request<SessionSummary[]>(`/api/decks/${seg(deck)}/sessions`),
  startSession: (deck: string) =>
    request<{ session_id: string }>(`/api/decks/${seg(deck)}/sessions`, { method: "POST" }),
  session: (deck: string, session: string) =>
    request<SessionDetail>(`/api/decks/${seg(deck)}/sessions/${seg(session)}`),
  present: (deck: string, session: string, question: string) =>
    request<PresentedQuestion>(`/api/decks/${seg(deck)}/sessions/${seg(session)}/questions/${seg(question)}`),
  submit: (deck: string, session: string, question: string, chosen: string) =>
    request<Graded>(`/api/decks/${seg(deck)}/sessions/${seg(session)}/answers`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question_id: question, chosen }),
    }),
  report: (deck: string, pre: string, post: string) =>
    request<Report>(`/api/decks/${seg(deck)}/report?pre=${seg(pre)}&post=${seg(post)}`),
};
