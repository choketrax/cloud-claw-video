import { Hono } from 'hono';
import type { AppEnv } from '../types';
import { GATEWAY_PORT } from '../config';
import { findExistingGatewayProcess, ensureGateway } from '../gateway';
import { restoreIfNeeded } from '../persistence';

/**
 * Public routes - NO Cloudflare Access authentication required
 *
 * These routes are mounted BEFORE the auth middleware is applied.
 * Includes: health checks, static assets, and public API endpoints.
 */
const publicRoutes = new Hono<AppEnv>();

// GET /sandbox-health - Health check endpoint
publicRoutes.get('/sandbox-health', (c) => {
  return c.json({
    status: 'ok',
    service: 'openclaw-sandbox',
    gateway_port: GATEWAY_PORT,
  });
});

// GET /logo.png - Serve logo from ASSETS binding
publicRoutes.get('/logo.png', (c) => {
  return c.env.ASSETS.fetch(c.req.raw);
});

// GET /logo-small.png - Serve small logo from ASSETS binding
publicRoutes.get('/logo-small.png', (c) => {
  return c.env.ASSETS.fetch(c.req.raw);
});

// GET /api/status - Public health check for gateway status (no auth required)
publicRoutes.get('/api/status', async (c) => {
  const sandbox = c.get('sandbox');

  try {
    let process = await findExistingGatewayProcess(sandbox);
    console.log('[api/status] existing process:', process?.id ?? 'none', process?.status ?? '');
    if (!process) {
      // Restore synchronously — restoreBackup is a fast RPC call (~1-3s).
      // This MUST happen before ensureGateway or the gateway starts without
      // the FUSE overlay.
      let restoreError: string | null = null;
      try {
        await restoreIfNeeded(sandbox, c.env.BACKUP_BUCKET);
      } catch (err) {
        restoreError = err instanceof Error ? err.message : String(err);
        console.error('[api/status] Restore failed:', restoreError);
      }

      // Start the gateway but DON'T wait for it to be ready.
      // ensureGateway with waitForReady:false just starts the process
      // (fast RPC, ~2-5s) without blocking on waitForPort (which takes
      // up to 180s and would exceed the 30s Worker CPU limit).
      // The loading page polls every 2s — subsequent polls will find
      // the process and check if the port is up.
      console.log('[api/status] No process found, starting gateway...');
      try {
        await ensureGateway(sandbox, c.env, { waitForReady: false });
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.error('[api/status] Gateway start failed:', msg);
        return c.json({ ok: false, status: 'start_failed', error: msg, restoreError });
      }
      return c.json({ ok: false, status: 'starting', restoreError });
    }

    // Process exists, check if it's actually responding
    // Try to reach the gateway with a short timeout
    try {
      await process.waitForPort(18789, { mode: 'tcp', timeout: 5000 });
      return c.json({ ok: true, status: 'running', processId: process.id });
    } catch {
      return c.json({ ok: false, status: 'not_responding', processId: process.id });
    }
  } catch (err) {
    return c.json({
      ok: false,
      status: 'error',
      error: err instanceof Error ? err.message : 'Unknown error',
    });
  }
});

// GET /_admin/assets/* - Admin UI static assets (CSS, JS need to load for login redirect)
// Assets are built to dist/client with base "/_admin/"
publicRoutes.get('/_admin/assets/*', async (c) => {
  const url = new URL(c.req.url);
  // Rewrite /_admin/assets/* to /assets/* for the ASSETS binding
  const assetPath = url.pathname.replace('/_admin/assets/', '/assets/');
  const assetUrl = new URL(assetPath, url.origin);
  return c.env.ASSETS.fetch(new Request(assetUrl.toString(), c.req.raw));
});


// GET /sleep - Instant one-click page to stop/wake container
publicRoutes.get('/sleep', async (c) => {
  const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Cloud-Claw Compute Controls</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #090d16;
      color: #e2e8f0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }
    .card {
      background: #111827;
      border: 1px solid #1f2937;
      border-radius: 16px;
      padding: 32px;
      max-width: 480px;
      width: 100%;
      text-align: center;
      box-shadow: 0 20px 25px -5px rgba(0,0,0,0.5);
    }
    .badge {
      display: inline-block;
      padding: 6px 14px;
      border-radius: 9999px;
      font-size: 13px;
      font-weight: 600;
      margin-bottom: 16px;
    }
    .badge.active { background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.4); }
    .badge.sleeping { background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.4); }
    h1 { font-size: 22px; font-weight: 700; color: #fff; margin-bottom: 8px; }
    p.desc { font-size: 14px; color: #94a3b8; line-height: 1.5; margin-bottom: 24px; }
    .btn {
      display: block;
      width: 100%;
      padding: 14px 20px;
      border-radius: 10px;
      font-size: 15px;
      font-weight: 600;
      border: none;
      cursor: pointer;
      text-decoration: none;
      margin-bottom: 12px;
      transition: all 0.2s;
    }
    .btn-stop {
      background: #e11d48;
      color: white;
    }
    .btn-stop:hover { background: #be123c; }
    .btn-chat {
      background: #2563eb;
      color: white;
    }
    .btn-chat:hover { background: #1d4ed8; }
    .btn-admin {
      background: #374151;
      color: #e5e7eb;
    }
    .btn-admin:hover { background: #4b5563; }
    .status-msg { margin-top: 16px; font-size: 13px; color: #38bdf8; min-height: 20px; }
  </style>
</head>
<body>
  <div class="card">
    <div id="badge" class="badge active">● Container Active</div>
    <h1>Cloud-Claw Compute Switch</h1>
    <p class="desc">
      Put the container to sleep instantly to stop CPU and memory compute billing ($0). Cloudflare will automatically wake it up when you reopen the chat.
    </p>
    <button id="stopBtn" class="btn btn-stop" onclick="stopContainer()">🛑 Stop Container ($0 Compute)</button>
    <a href="/?token=2xkM%25%26Fr3zT80oBC*8tobtlom)" class="btn btn-chat">💬 Open Chat (Auto-Wakes)</a>
    <a href="/_admin/" class="btn btn-admin">⚙️ Admin Dashboard</a>
    <div id="statusMsg" class="status-msg"></div>
  </div>
  <script>
    async function stopContainer() {
      const btn = document.getElementById('stopBtn');
      const badge = document.getElementById('badge');
      const msg = document.getElementById('statusMsg');
      btn.disabled = true;
      btn.innerText = 'Putting container to sleep...';
      try {
        const res = await fetch('/api/sleep', { method: 'POST' });
        const data = await res.json();
        badge.className = 'badge sleeping';
        badge.innerText = '⏸ Container Sleeping ($0 Compute)';
        btn.innerText = '✓ Stopped (Sleeping)';
        msg.innerText = 'Container is asleep! Compute billing paused. Click Open Chat anytime to wake it up.';
      } catch (err) {
        btn.disabled = false;
        btn.innerText = '🛑 Stop Container ($0 Compute)';
        msg.innerText = 'Error stopping container: ' + err.message;
      }
    }
  </script>
</body>
</html>`;
  return c.html(html);
});

// POST /api/sleep - Public endpoint for the sleep button
publicRoutes.post('/api/sleep', async (c) => {
  const sandbox = c.get('sandbox');
  try {
    await sandbox.destroy();
    return c.json({ success: true, message: 'Container destroyed and put to sleep' });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return c.json({ success: false, error: msg }, 500);
  }
});

export { publicRoutes };
