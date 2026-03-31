import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  deleteBybitKeys,
  fetchBybitKeyStatus,
  saveBybitKeys,
  setApiSecret,
  verifyBybitKeys,
} from "../api.js";

export default function ApiKeysPage() {
  const [apiSecret, setSecretLocal] = useState(() => localStorage.getItem("apiSecret") || "");
  const [apiKey, setApiKey] = useState("");
  const [apiSecretKey, setApiSecretKey] = useState("");
  const [isTestnet, setIsTestnet] = useState(true);
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [verifyMsg, setVerifyMsg] = useState("");

  useEffect(() => {
    setApiSecret(apiSecret);
  }, [apiSecret]);

  const loadStatus = async () => {
    setError("");
    try {
      setStatus(await fetchBybitKeyStatus());
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
      setStatus(null);
    }
  };

  useEffect(() => {
    if (apiSecret) loadStatus();
  }, [apiSecret]);

  const onSave = async () => {
    setBusy(true);
    setError("");
    setVerifyMsg("");
    try {
      await saveBybitKeys({
        api_key: apiKey.trim(),
        api_secret: apiSecretKey.trim(),
        is_testnet: isTestnet,
      });
      setApiKey("");
      setApiSecretKey("");
      await loadStatus();
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
    } finally {
      setBusy(false);
    }
  };

  const onVerify = async () => {
    setBusy(true);
    setError("");
    setVerifyMsg("");
    try {
      const r = await verifyBybitKeys();
      setVerifyMsg(`Подключение OK (${r.mode}${r.is_testnet != null ? `, testnet=${r.is_testnet}` : ""})`);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
    } finally {
      setBusy(false);
    }
  };

  const onDelete = async () => {
    if (!window.confirm("Удалить сохранённые ключи из БД? Торговля перейдёт на .env или остановится.")) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      await deleteBybitKeys();
      await loadStatus();
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
    } finally {
      setBusy(false);
    }
  };

  const onSaveSecret = () => {
    localStorage.setItem("apiSecret", apiSecret);
    setApiSecret(apiSecret);
  };

  return (
    <div style={{ padding: 24, maxWidth: 720, margin: "0 auto" }}>
      <nav style={{ marginBottom: 16, display: "flex", gap: 16 }}>
        <Link to="/">← Главная</Link>
      </nav>
      <h1 style={{ marginTop: 0 }}>API-ключи Bybit</h1>
      <p style={{ opacity: 0.85 }}>
        Ключи хранятся на сервере в зашифрованном виде (Fernet, мастер-ключ <code>SECRET_KEY</code> в{" "}
        <code>.env</code>). Секреты никогда не возвращаются API — только статус.
      </p>

      <div className="card">
        <h2>Секрет панели (X-API-Secret)</h2>
        <p>Нужен для всех операций с ключами.</p>
        <input
          style={{ width: "100%", maxWidth: 420 }}
          type="password"
          placeholder="API_SECRET сервера"
          value={apiSecret}
          onChange={(e) => setSecretLocal(e.target.value)}
        />
        <div style={{ marginTop: 8 }}>
          <button type="button" onClick={onSaveSecret}>
            Сохранить в браузере
          </button>
        </div>
      </div>

      {error && (
        <div className="card" style={{ borderColor: "#842029", color: "#f8d7da" }}>
          {typeof error === "string" ? error : JSON.stringify(error)}
        </div>
      )}

      <div className="card">
        <h2>Статус</h2>
        {status ? (
          <ul style={{ listStyle: "none", padding: 0 }}>
            <li>
              Подключено: <strong>{status.configured ? "да" : "нет"}</strong>
            </li>
            <li>Источник: {status.source}</li>
            <li>
              Режим:{" "}
              {status.is_testnet == null
                ? "—"
                : status.is_testnet
                  ? "testnet"
                  : "live"}
            </li>
            {status.credentials_usable != null && (
              <li>
                Запись в БД читается: {status.credentials_usable ? "да" : "нет"}
              </li>
            )}
          </ul>
        ) : (
          <p>Укажите секрет панели и обновите страницу.</p>
        )}
        <button type="button" className="secondary" disabled={busy || !apiSecret} onClick={loadStatus}>
          Обновить статус
        </button>
      </div>

      <div className="card">
        <h2>Сохранить ключи Bybit</h2>
        <p>После сохранения поля очищаются; ключи не отображаются повторно.</p>
        <label style={{ display: "block", marginBottom: 8 }}>
          API Key
          <input
            style={{ width: "100%", maxWidth: 420, display: "block" }}
            type="password"
            autoComplete="off"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
        </label>
        <label style={{ display: "block", marginBottom: 8 }}>
          API Secret
          <input
            style={{ width: "100%", maxWidth: 420, display: "block" }}
            type="password"
            autoComplete="off"
            value={apiSecretKey}
            onChange={(e) => setApiSecretKey(e.target.value)}
          />
        </label>
        <label style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12 }}>
          <input
            type="checkbox"
            checked={isTestnet}
            onChange={(e) => setIsTestnet(e.target.checked)}
          />
          Testnet (песочница Bybit)
        </label>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            type="button"
            disabled={busy || !apiSecret || !apiKey || !apiSecretKey}
            onClick={onSave}
          >
            Сохранить и проверить на бирже
          </button>
          <button type="button" className="secondary" disabled={busy || !apiSecret} onClick={onVerify}>
            Проверить подключение
          </button>
        </div>
        {verifyMsg && <p style={{ marginTop: 12, color: "#49d6a5" }}>{verifyMsg}</p>}
      </div>

      <div className="card">
        <h2>Удалить ключи из БД</h2>
        <button type="button" className="danger" disabled={busy || !apiSecret} onClick={onDelete}>
          Удалить сохранённые ключи
        </button>
      </div>

      <footer style={{ opacity: 0.6, fontSize: 13, marginTop: 24 }}>
        Прод: вынесите <code>SECRET_KEY</code> в AWS Secrets Manager / KMS; см. <code>docs/API_KEYS.md</code>.
      </footer>
    </div>
  );
}
