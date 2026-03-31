/**
 * PM2 (Windows/Linux): npm i -g pm2 && pm2 start deploy/ecosystem.config.cjs
 * Пути замените на абсолютные под вашу установку.
 */
module.exports = {
  apps: [
    {
      name: "scalper-backend",
      cwd: __dirname + "/../backend",
      script: ".venv/Scripts/python.exe",
      args: "-m uvicorn app.main:app --host 0.0.0.0 --port 8000",
      interpreter: "none",
      autorestart: true,
      max_restarts: 50,
      min_uptime: "10s",
      env: { NODE_ENV: "production" },
    },
    {
      name: "scalper-frontend",
      cwd: __dirname + "/../frontend",
      script: "npm",
      args: "run preview -- --host 0.0.0.0 --port 5173",
      interpreter: "none",
      autorestart: true,
    },
    {
      name: "scalper-watchdog",
      cwd: __dirname + "/../backend",
      script: ".venv/Scripts/python.exe",
      args: "-m app.services.watchdog",
      interpreter: "none",
      autorestart: true,
      env: { WATCHDOG_ENABLED: "true" },
    },
  ],
};
