/**
 * Webhook relay: Telegram posts each message here the moment it's sent; the
 * relay queues it and starts the Assistant GM workflow, so replies come in
 * about half a minute instead of at the next half-hourly run.
 *
 *   POST /telegram  Telegram's webhook, checked against WEBHOOK_SECRET
 *   POST /updates   {"offset": n} with RELAY_TOKEN as bearer: drops queued
 *                   updates below n and returns the rest, like getUpdates
 *
 * The queue is what makes this safe: GitHub keeps only one waiting run per
 * concurrency group (cancelling older ones) and a run can fail to start, but
 * every message waits here until a run has read it.
 *
 * It also starts the half-hourly runs (the crons in wrangler.toml). GitHub's
 * own schedule is best-effort and in practice ran about 3 times a day, missing
 * the evening briefing; Cloudflare's crons fire on time. The workflow's cron
 * stays as a backup.
 */

export default {
  async fetch(request, env, ctx) {
    const { pathname } = new URL(request.url);
    if (request.method === "POST" && pathname === "/telegram") return receive(request, env, ctx);
    if (request.method === "POST" && pathname === "/updates") return updates(request, env);
    return new Response("Not found", { status: 404 });
  },
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(startRun(env));
  },
};

async function receive(request, env, ctx) {
  if (!matches(request.headers.get("X-Telegram-Bot-Api-Secret-Token"), env.WEBHOOK_SECRET)) {
    return new Response("Forbidden", { status: 403 });
  }
  const update = await request.json();
  // If this throws, Telegram gets a 500 and retries, so nothing is lost.
  await env.DB.prepare("INSERT OR IGNORE INTO updates (update_id, body) VALUES (?, ?)")
    .bind(update.update_id, JSON.stringify(update))
    .run();
  ctx.waitUntil(startRun(env));
  return new Response("ok");
}

async function updates(request, env) {
  if (!matches(request.headers.get("Authorization"), `Bearer ${env.RELAY_TOKEN}`)) {
    return new Response("Forbidden", { status: 403 });
  }
  const { offset = 0 } = await request.json();
  const [, queued, error] = await env.DB.batch([
    env.DB.prepare("DELETE FROM updates WHERE update_id < ?").bind(offset),
    env.DB.prepare("SELECT body FROM updates ORDER BY update_id"),
    env.DB.prepare("SELECT value FROM meta WHERE key = 'dispatch_error'"),
  ]);
  return Response.json({
    ok: true,
    result: queued.results.map((row) => JSON.parse(row.body)),
    dispatch_error: error.results[0]?.value ?? null,
  });
}

/** Start the workflow; remember the last failure so the next run can report it. */
async function startRun(env) {
  let error = null;
  try {
    const resp = await fetch(
      `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${env.GITHUB_WORKFLOW}/dispatches`,
      {
        method: "POST",
        headers: {
          // Secrets pasted in a terminal can carry a trailing newline.
          Authorization: `Bearer ${env.GITHUB_TOKEN.trim()}`,
          Accept: "application/vnd.github+json",
          "User-Agent": "assistant-gm-relay",
          "X-GitHub-Api-Version": "2022-11-28",
        },
        body: JSON.stringify({ ref: "main" }),
      },
    );
    if (!resp.ok) {
      const detail = (await resp.text()).replace(/\s+/g, " ").slice(0, 200);
      error = `GitHub ${resp.status}: ${detail} [token: ${tokenShape(env.GITHUB_TOKEN)}]`;
    }
  } catch (e) {
    error = `GitHub unreachable: ${e}`;
  }
  await env.DB.prepare("INSERT OR REPLACE INTO meta (key, value) VALUES ('dispatch_error', ?)")
    .bind(error)
    .run();
}

/** What's wrong with a token, without revealing it: "github_pat_..., 93 chars". */
function tokenShape(token) {
  const t = token.trim();
  const odd = [...t].filter((c) => !/[A-Za-z0-9_]/.test(c)).map((c) => `U+${c.codePointAt(0).toString(16)}`);
  const prefix = (t.match(/^(github_pat_|ghp_|gho_)/) || ["unknown prefix "])[0];
  return `${prefix}..., ${t.length} chars${odd.length ? `, unexpected ${[...new Set(odd)].join(" ")}` : ""}`;
}

function matches(given, expected) {
  if (!given || !expected) return false;
  const a = new TextEncoder().encode(given);
  const b = new TextEncoder().encode(expected);
  return a.byteLength === b.byteLength && crypto.subtle.timingSafeEqual(a, b);
}
