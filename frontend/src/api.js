import axios from "axios";

const base = import.meta.env.VITE_API_BASE || "";

export const api = axios.create({
  baseURL: base || "",
  timeout: 60000,
});

export function setApiSecret(secret) {
  if (secret) {
    api.defaults.headers.common["X-API-Secret"] = secret;
  } else {
    delete api.defaults.headers.common["X-API-Secret"];
  }
}

export async function fetchSettings() {
  const { data } = await api.get("/api/settings");
  return data;
}

export async function patchSettings(payload) {
  const { data } = await api.patch("/api/settings", payload);
  return data;
}

export async function confirmRealMode(secret, phrase, acknowledgeRisks = true) {
  const { data } = await api.post(
    "/api/settings/real-mode",
    {
      api_secret: secret,
      confirm_phrase: phrase,
      acknowledge_risks: acknowledgeRisks,
    },
    { headers: { "X-API-Secret": secret } }
  );
  return data;
}

export async function fetchBybitKeyStatus() {
  return (await api.get("/api/keys/bybit/status")).data;
}

export async function saveBybitKeys(payload) {
  return (await api.post("/api/keys/bybit", payload)).data;
}

export async function verifyBybitKeys() {
  return (await api.post("/api/keys/bybit/verify", {})).data;
}

export async function deleteBybitKeys() {
  return (await api.delete("/api/keys/bybit")).data;
}

export async function botStart() {
  return (await api.post("/api/bot/start")).data;
}

export async function botStop() {
  return (await api.post("/api/bot/stop")).data;
}

export async function botStatus() {
  return (await api.get("/api/bot/status")).data;
}

export async function fetchTrades() {
  // Тяжёлый эндпоинт; по умолчанию берём меньше строк, чтобы не душить БД/бекенд.
  return (await api.get("/api/trades", { params: { limit: 120 } })).data;
}

export async function fetchPositions() {
  return (await api.get("/api/positions")).data;
}

export async function fetchEquity() {
  return (await api.get("/api/equity")).data;
}

export async function fetchSystemStatus() {
  return (await api.get("/api/system/status")).data;
}

export async function fetchScanSnapshot() {
  return (await api.get("/api/scan/snapshot")).data;
}

export async function fetchStrategiesSummary() {
  return (await api.get("/api/strategies/summary")).data;
}

export async function fetchTradingControl() {
  return (await api.get("/api/trading/control")).data;
}

export async function postTradingPause(reason) {
  return (await api.post("/api/trading/pause", { reason: reason || "manual_pause" })).data;
}

export async function postTradingResume() {
  return (await api.post("/api/trading/resume", {})).data;
}

export async function runBacktest(symbol) {
  return (await api.post("/api/ml/backtest", null, { params: { symbol } })).data;
}

export async function trainModel(symbol) {
  return (await api.post("/api/ml/train", null, { params: { symbol } })).data;
}
