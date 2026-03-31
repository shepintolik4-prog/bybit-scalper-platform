import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { bucketRuntimeKeys, formatRuntimeValue, RUNTIME_KEY_HINTS } from "../dashboardRuntimeGroups.js";
import {
  botStart,
  botStatus,
  botStop,
  confirmRealMode,
  fetchEquity,
  fetchPositions,
  fetchSettings,
  fetchTrades,
  fetchScanSnapshot,
  fetchStrategiesSummary,
  fetchTradingControl,
  fetchSystemStatus,
  patchSettings,
  postTradingPause,
  postTradingResume,
  setApiSecret,
} from "../api.js";

function fmtTime(ts) {
  try {
    return new Date(ts).toLocaleString("ru-RU");
  } catch {
    return String(ts);
  }
}

/** Краткая метка из explanation без вывода всего JSON в таблицу */
function strategyLabel(ex) {
  if (!ex || typeof ex !== "object") return "—";
  if (ex.strategy_id) return String(ex.strategy_id);
  const ru = ex.trade_explanation_ru;
  if (ru && typeof ru === "object" && ru.стратегия != null) return String(ru.стратегия);
  return "—";
}

const DB_SETTINGS_HELP = {
  leverage: {
    title: "Плечо (маржа)",
    hint: "Множитель к номиналу позиции на бирже. Выше плечо — выше прибыль/убыток на 1% движения цены и риск ликвидации. В БД хранится выбранное значение; бот применяет его при открытии.",
  },
  risk_per_trade_pct: {
    title: "Риск на сделку, %",
    hint: "Доля эквити (или виртуального баланса в paper), которую движок закладывает под стоп на одну сделку. Больше процент — крупнее позиции и быстрее растёт волатильность кривой.",
  },
  max_drawdown_pct: {
    title: "Стоп по просадке, %",
    hint: "Порог просадки от пика эквити: при превышении обычно останавливаются новые входы или срабатывают защитные правила (см. движок). Не путать с дневным лимитом из .env.",
  },
  max_open_positions: {
    title: "Максимум открытых позиций",
    hint: "Сколько сделок одновременно может держать бот. Ограничивает корреляционный риск и нагрузку на API.",
  },
  virtual_balance: {
    title: "Виртуальный баланс (paper)",
    hint: "Стартовый/текущий опорный капитал для расчёта размера позиций в тестовом режиме. На live реальный баланс берётся с биржи.",
  },
};

function SettingField({ id, children, valuePreview }) {
  const meta = DB_SETTINGS_HELP[id];
  if (!meta) return children;
  return (
    <label style={{ display: "grid", gap: 6 }}>
      <span>
        <strong>{meta.title}</strong>
        <span style={{ opacity: 0.75, fontWeight: 400 }}> — сейчас: {valuePreview}</span>
      </span>
      <span style={{ fontSize: 12, opacity: 0.82, lineHeight: 1.4 }}>{meta.hint}</span>
      {children}
    </label>
  );
}

function fmtSigned(n, digits = 2, suffix = "") {
  const x = Number(n);
  if (!Number.isFinite(x)) return "—";
  const s = x > 0 ? "+" : "";
  return `${s}${x.toFixed(digits)}${suffix}`;
}

export default function Dashboard() {
  const [settings, setSettings] = useState(null);
  const [trades, setTrades] = useState([]);
  const [positions, setPositions] = useState([]);
  const [equity, setEquity] = useState([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [apiSecret, setSecretLocal] = useState(
    () => localStorage.getItem("apiSecret") || ""
  );
  const [confirmPhrase, setConfirmPhrase] = useState("");
  const [riskAcknowledged, setRiskAcknowledged] = useState(false);
  const [scanSnap, setScanSnap] = useState(null);
  const [stratSum, setStratSum] = useState(null);
  const [tradingCtrl, setTradingCtrl] = useState(null);
  const [systemStatus, setSystemStatus] = useState(null);
  const inFlightRef = useRef(false);

  useEffect(() => {
    setApiSecret(apiSecret);
  }, [apiSecret]);

  const loadAll = async () => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    setError("");
    try {
      const [s, t, p, e, sc, st, tc, sys] = await Promise.all([
        fetchSettings(),
        fetchTrades(),
        fetchPositions(),
        fetchEquity(),
        fetchScanSnapshot().catch(() => null),
        fetchStrategiesSummary().catch(() => null),
        fetchTradingControl().catch(() => null),
        fetchSystemStatus().catch(() => null),
      ]);
      setSettings(s);
      setTrades(t);
      setPositions(p);
      setEquity(e);
      setScanSnap(sc);
      setStratSum(st);
      setTradingCtrl(tc);
      setSystemStatus(sys);
    } catch (e) {
      setError(e?.message || String(e));
    } finally {
      inFlightRef.current = false;
    }
  };

  const loadFast = async () => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    try {
      const [p, sc, tc, sys] = await Promise.all([
        fetchPositions(),
        fetchScanSnapshot().catch(() => null),
        fetchTradingControl().catch(() => null),
        fetchSystemStatus().catch(() => null),
      ]);
      setPositions(p);
      setScanSnap(sc);
      setTradingCtrl(tc);
      setSystemStatus(sys);
    } catch (e) {
      setError(e?.message || String(e));
    } finally {
      inFlightRef.current = false;
    }
  };

  useEffect(() => {
    loadAll();
    const id = setInterval(() => {
      if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
      // Частый polling только для "живых" данных (позиции/скан/статус),
      // чтобы не забивать БД тяжёлыми /api/trades и /api/settings.
      if (positions.length > 0) loadFast();
      else loadAll();
    }, positions.length > 0 ? 2500 : 8000);
    return () => clearInterval(id);
  }, [positions.length]);

  const chartData = useMemo(() => {
    return equity.map((x) => ({
      t: fmtTime(x.ts),
      equity: Number(x.equity.toFixed(2)),
      balance: Number(x.balance.toFixed(2)),
    }));
  }, [equity]);

  const runtimeBuckets = useMemo(() => {
    const br = settings?.bot_runtime;
    if (!br || typeof br !== "object") return [];
    return bucketRuntimeKeys(Object.keys(br));
  }, [settings]);

  const closedPnlBars = useMemo(() => {
    const rows = (trades || [])
      .filter((t) => t.closed_at != null && t.pnl_usdt != null && !Number.isNaN(Number(t.pnl_usdt)))
      .slice(0, 40)
      .reverse();
    return rows.map((t, i) => ({
      name: `${t.symbol?.split("/")[0] || "?"}-${i + 1}`,
      fullSymbol: t.symbol,
      pnl: Number(Number(t.pnl_usdt).toFixed(4)),
      t: fmtTime(t.closed_at),
    }));
  }, [trades]);

  const onSaveSecret = () => {
    localStorage.setItem("apiSecret", apiSecret);
    setApiSecret(apiSecret);
  };

  const onPatch = async (payload) => {
    setBusy(true);
    try {
      const s = await patchSettings(payload);
      setSettings(s);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
    } finally {
      setBusy(false);
    }
  };

  const onReal = async () => {
    setBusy(true);
    try {
      const s = await confirmRealMode(apiSecret, confirmPhrase, riskAcknowledged);
      setSettings(s);
      setConfirmPhrase("");
      setRiskAcknowledged(false);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
    } finally {
      setBusy(false);
    }
  };

  const fmtApiErr = (e) => {
    const d = e?.response?.data?.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map((x) => x?.msg || JSON.stringify(x)).join("; ");
    return e?.message || String(e);
  };

  const onBotStart = async () => {
    setBusy(true);
    setError("");
    try {
      await botStart();
      await loadAll();
    } catch (e) {
      setError(fmtApiErr(e));
    } finally {
      setBusy(false);
    }
  };

  const onBotStop = async () => {
    setBusy(true);
    setError("");
    try {
      await botStop();
      await loadAll();
    } catch (e) {
      setError(fmtApiErr(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ padding: 24, maxWidth: 1200, margin: "0 auto" }}>
      <nav style={{ marginBottom: 16, display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
        <Link to="/">Главная</Link>
        <Link to="/settings/api-keys">API-ключи Bybit</Link>
      </nav>
      <h1 style={{ marginTop: 0 }}>Панель скальпера Bybit (USDT perpetual)</h1>
      <p style={{ opacity: 0.85 }}>
        Локальный режим: бэкенд FastAPI, ML (XGBoost + LSTM), PostgreSQL. Интерфейс на русском.
      </p>
      {error && (
        <div className="card" style={{ borderColor: "#842029", color: "#f8d7da" }}>
          Ошибка: {typeof error === "string" ? error : JSON.stringify(error)}
        </div>
      )}

      {systemStatus && (
        <div
          className="card"
          style={{
            borderColor:
              systemStatus.health === "STOPPED"
                ? "#842029"
                : systemStatus.health === "WARNING"
                  ? "#b8860b"
                  : "#2d6a4f",
          }}
        >
          <h2 style={{ marginTop: 0 }}>Состояние системы (production engine)</h2>
          <p style={{ marginTop: 0 }}>
            <span className="badge">Health: {systemStatus.health}</span>{" "}
            <span className="badge">
              Kill switch: {systemStatus.kill_switch_active ? "ДА" : "нет"}
            </span>{" "}
            <span className="badge">
              Circuit breaker: {systemStatus.circuit_breaker_open ? "открыт" : "нет"}
            </span>{" "}
            <span className="badge">
              Сверка БД: {systemStatus.consistency_last_ok ? "OK" : "есть расхождения"}
            </span>
          </p>
          {systemStatus.pause_reason && (
            <p style={{ fontSize: 13, opacity: 0.9 }}>
              Пауза: <code>{systemStatus.pause_reason}</code>
              {systemStatus.pause_source && (
                <>
                  {" "}
                  <small>({systemStatus.pause_source})</small>
                </>
              )}
            </p>
          )}
          {systemStatus.consistency_issues?.length > 0 && (
            <ul style={{ fontSize: 12, marginBottom: 0 }}>
              {systemStatus.consistency_issues.slice(0, 8).map((x) => (
                <li key={x}>
                  <code>{x}</code>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="card">
        <h2>Безопасность API</h2>
        <p>
          Заголовок <code>X-API-Secret</code> должен совпадать с <code>API_SECRET</code> на сервере.
          Он обязателен для старт/стоп бота, сохранения настроек (PATCH), ML (train/backtest) и
          портфельной аллокации; для live — вместе с фразой подтверждения.
        </p>
        <input
          style={{ width: "100%", maxWidth: 420 }}
          placeholder="API_SECRET из .env (например dev-secret)"
          value={apiSecret}
          onChange={(e) => setSecretLocal(e.target.value)}
        />
        <div style={{ marginTop: 8 }}>
          <button onClick={onSaveSecret}>Сохранить секрет в браузере</button>
        </div>
      </div>

      <div className="grid2">
        <div className="card">
          <h2>Управление ботом</h2>
          {settings && (
            <div>
              <p>
                Режим:{" "}
                <span className="badge">{settings.paper_mode ? "Тест (paper)" : "Реальный"}</span>{" "}
                · Бот:{" "}
                <span className="badge">{settings.bot_enabled ? "включён" : "выключен"}</span>
              </p>
              <p>Реальный режим доступен: {settings.real_available ? "да (env)" : "нет"}</p>
              <p style={{ fontSize: 13, opacity: 0.88, marginTop: 0 }}>
                Старт/стоп бота требуют заголовок <code>X-API-Secret</code>: введите значение{" "}
                <code>API_SECRET</code> из <code>backend/.env</code> выше, нажмите «Сохранить секрет в
                браузере», затем «Включить бота». Иначе сервер ответит 401 и кнопка «молчала» (раньше ошибка
                не показывалась).
              </p>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() => onPatch({ paper_mode: true })}
                >
                  Режим: только тест (paper)
                </button>
                <button disabled={busy} onClick={onBotStart}>
                  Включить бота
                </button>
                <button className="secondary" disabled={busy} onClick={onBotStop}>
                  Выключить бота
                </button>
                <button className="secondary" onClick={() => botStatus().then(console.log)}>
                  Статус
                </button>
                <button className="secondary" onClick={loadAll}>
                  Обновить данные
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="card">
          <h2>Настройки риска (база данных)</h2>
          <p style={{ marginTop: 0, fontSize: 13, opacity: 0.85 }}>
            Сохраняются в PostgreSQL. Отличаются от дефолтов в <code>.env</code> — см. блок «Параметры
            окружения» ниже.
          </p>
          {settings && (
            <div style={{ display: "grid", gap: 14 }}>
              <SettingField id="leverage" valuePreview={settings.leverage}>
                <input
                  type="number"
                  min={1}
                  max={125}
                  value={settings.leverage}
                  onChange={(e) =>
                    setSettings({ ...settings, leverage: Number(e.target.value) })
                  }
                />
              </SettingField>
              <SettingField id="risk_per_trade_pct" valuePreview={`${settings.risk_per_trade_pct}%`}>
                <input
                  type="number"
                  step="0.1"
                  value={settings.risk_per_trade_pct}
                  onChange={(e) =>
                    setSettings({ ...settings, risk_per_trade_pct: Number(e.target.value) })
                  }
                />
              </SettingField>
              <SettingField id="max_drawdown_pct" valuePreview={`${settings.max_drawdown_pct}%`}>
                <input
                  type="number"
                  step="0.5"
                  value={settings.max_drawdown_pct}
                  onChange={(e) =>
                    setSettings({ ...settings, max_drawdown_pct: Number(e.target.value) })
                  }
                />
              </SettingField>
              <SettingField id="max_open_positions" valuePreview={settings.max_open_positions}>
                <input
                  type="number"
                  min={1}
                  value={settings.max_open_positions}
                  onChange={(e) =>
                    setSettings({ ...settings, max_open_positions: Number(e.target.value) })
                  }
                />
              </SettingField>
              <SettingField id="virtual_balance" valuePreview={`${settings.virtual_balance} USDT`}>
                <input
                  type="number"
                  step="100"
                  value={settings.virtual_balance}
                  onChange={(e) =>
                    setSettings({ ...settings, virtual_balance: Number(e.target.value) })
                  }
                />
              </SettingField>
              <button
                disabled={busy}
                onClick={() =>
                  onPatch({
                    leverage: settings.leverage,
                    risk_per_trade_pct: settings.risk_per_trade_pct,
                    max_drawdown_pct: settings.max_drawdown_pct,
                    max_open_positions: settings.max_open_positions,
                    virtual_balance: settings.virtual_balance,
                  })
                }
              >
                Сохранить в базу
              </button>
            </div>
          )}
        </div>
      </div>

      {settings?.bot_runtime && Object.keys(settings.bot_runtime).length > 0 && (
        <div className="card">
          <h2>Параметры окружения (.env) — только чтение</h2>
          <p style={{ marginTop: 0, fontSize: 13, opacity: 0.85 }}>
            Снимок переменных backend без секретов. После правок в <code>.env</code> перезапустите
            воркер. Значения в форме выше (БД) могут отличаться.
          </p>
          <div style={{ display: "grid", gap: 10 }}>
            {runtimeBuckets.map((bucket) => (
              <details
                key={bucket.id}
                style={{ border: "1px solid #223055", borderRadius: 8, padding: "8px 12px" }}
              >
                <summary style={{ cursor: "pointer", fontWeight: 600 }}>
                  {bucket.title}{" "}
                  <span style={{ opacity: 0.65, fontWeight: 400 }}>({bucket.keys.length})</span>
                </summary>
                <p style={{ fontSize: 12, opacity: 0.82, margin: "8px 0" }}>{bucket.blurb}</p>
                <div style={{ overflowX: "auto" }}>
                  <table style={{ fontSize: 12 }}>
                    <thead>
                      <tr>
                        <th>Параметр</th>
                        <th>Сейчас</th>
                      </tr>
                    </thead>
                    <tbody>
                      {bucket.keys.map((key) => {
                        const hint = RUNTIME_KEY_HINTS[key];
                        return (
                          <tr key={key} title={hint || undefined}>
                            <td>
                              <code>{key}</code>
                              {hint && (
                                <div style={{ opacity: 0.78, marginTop: 4, maxWidth: 520 }}>
                                  {hint}
                                </div>
                              )}
                            </td>
                            <td>
                              <code>{formatRuntimeValue(settings.bot_runtime[key])}</code>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </details>
            ))}
          </div>
        </div>
      )}

      <div className="card">
        <h2>Переключение в реальную торговлю</h2>
        <p>
          Двойная защита: переменная <code>CONFIRM_REAL_TRADING=true</code> на сервере + фраза{" "}
          <code>ENABLE_LIVE</code> + секрет + явное подтверждение рисков.
        </p>
        <label style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
          <input
            type="checkbox"
            checked={riskAcknowledged}
            onChange={(e) => setRiskAcknowledged(e.target.checked)}
          />
          Я понимаю риск потери капитала и подтверждаю включение реальной торговли
        </label>
        <input
          placeholder="Фраза ENABLE_LIVE"
          value={confirmPhrase}
          onChange={(e) => setConfirmPhrase(e.target.value)}
          style={{ width: "100%", maxWidth: 360 }}
        />
        <div style={{ marginTop: 8 }}>
          <button
            className="danger"
            disabled={busy || !riskAcknowledged}
            onClick={onReal}
            title={!riskAcknowledged ? "Отметьте подтверждение рисков" : ""}
          >
            Включить реальный режим
          </button>
        </div>
      </div>

      <div className="card">
        <h2>Скан рынка и ранжирование</h2>
        {scanSnap ? (
          <div style={{ display: "grid", gap: 12 }}>
            {scanSnap.strategy_panel?.tick_skip?.detail_ru && (
              <p
                style={{
                  margin: 0,
                  padding: "10px 12px",
                  borderRadius: 8,
                  background: "rgba(255, 193, 7, 0.12)",
                  border: "1px solid rgba(255, 193, 7, 0.35)",
                  fontSize: 14,
                }}
              >
                <b>Скан ограничен:</b> {scanSnap.strategy_panel.tick_skip.detail_ru}
                {scanSnap.strategy_panel.tick_skip.reason && (
                  <span style={{ opacity: 0.75 }}> ({scanSnap.strategy_panel.tick_skip.reason})</span>
                )}
              </p>
            )}
            <p style={{ margin: 0, opacity: 0.9 }}>
              Символов в последнем цикле: <b>{scanSnap.scanned_count ?? scanSnap.scanned_symbols?.length ?? "—"}</b>
              {scanSnap.selected_symbol && (
                <>
                  {" "}
                  · лучший кандидат цикла: <b>{scanSnap.selected_symbol}</b>
                  {scanSnap.selected_composite != null && (
                    <> (composite {Number(scanSnap.selected_composite).toFixed(4)})</>
                  )}
                </>
              )}
            </p>
            <div>
              <h3 style={{ margin: "8px 0 4px" }}>Топ-10 сигналов</h3>
              <table>
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Символ</th>
                    <th>edge</th>
                    <th>conf</th>
                    <th>composite</th>
                    <th>режим</th>
                    <th>стратегия</th>
                  </tr>
                </thead>
                <tbody>
                  {(scanSnap.top_signals || []).map((row, i) => (
                    <tr key={`${row.symbol}-${i}`}>
                      <td>{i + 1}</td>
                      <td>{row.symbol}</td>
                      <td>{row.combined_edge ?? "—"}</td>
                      <td>{row.confidence ?? "—"}</td>
                      <td>{row.composite_score ?? "—"}</td>
                      <td>{row.regime ?? "—"}</td>
                      <td>
                        <code>{row.strategy_id ?? "—"}</code>
                      </td>
                    </tr>
                  ))}
                  {(!scanSnap.top_signals || scanSnap.top_signals.length === 0) && (
                    <tr>
                      <td colSpan={7}>
                        {scanSnap.strategy_panel?.tick_skip
                          ? "Нет сигналов: полный проход по свечам не выполнялся (см. жёлтый блок выше)."
                          : "Нет кандидатов в последнем тике"}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <details>
              <summary>Все просканированные символы ({(scanSnap.scanned_symbols || []).length})</summary>
              <pre style={{ fontSize: 11, maxHeight: 200, overflow: "auto" }}>
                {(scanSnap.scanned_symbols || []).join(", ")}
              </pre>
            </details>
            <div>
              <h3 style={{ margin: "8px 0 4px" }}>Отклонённые сигналы (последние)</h3>
              <table>
                <thead>
                  <tr>
                    <th>Время</th>
                    <th>Символ</th>
                    <th>Причина</th>
                  </tr>
                </thead>
                <tbody>
                  {(scanSnap.rejects || []).slice(-25).reverse().map((r, i) => (
                    <tr key={`${r.symbol}-${r.reason}-${i}`}>
                      <td>{r.ts ? fmtTime(r.ts * 1000) : "—"}</td>
                      <td>{r.symbol}</td>
                      <td>
                        <code>{r.reason}</code>
                      </td>
                    </tr>
                  ))}
                  {(!scanSnap.rejects || scanSnap.rejects.length === 0) && (
                    <tr>
                      <td colSpan={3}>Пока нет записей</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        ) : (
          <p>Нет данных скана (бэкенд не ответил или бот ещё не делал тик).</p>
        )}
      </div>

      <div className="card">
        <h2>Стратегии и контроль торговли</h2>
        {tradingCtrl && (
          <p style={{ marginTop: 0 }}>
            Пауза:{" "}
            <span className="badge">{tradingCtrl.paused ? "да" : "нет"}</span>
            {tradingCtrl.reason && (
              <>
                {" "}
                — <code>{tradingCtrl.reason}</code>
              </>
            )}
            {tradingCtrl.source && (
              <>
                {" "}
                <small>({tradingCtrl.source})</small>
              </>
            )}
          </p>
        )}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
          <button
            type="button"
            className="secondary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await postTradingPause("manual_dashboard");
                await loadAll();
              } catch (err) {
                setError(err?.response?.data?.detail || err.message);
              } finally {
                setBusy(false);
              }
            }}
          >
            Пауза (API)
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await postTradingResume();
                await loadAll();
              } catch (err) {
                setError(err?.response?.data?.detail || err.message);
              } finally {
                setBusy(false);
              }
            }}
          >
            Снять паузу
          </button>
        </div>
        <p style={{ opacity: 0.8, fontSize: 13 }}>
          Требуется <code>X-API-Secret</code> в браузере (как для real-mode). Сводка стратегий за цикл — из
          последнего тика.
        </p>
        {scanSnap?.strategy_panel && Object.keys(scanSnap.strategy_panel).length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <h3 style={{ margin: "8px 0 4px" }}>Маршрутизатор (последний тик)</h3>
            <pre style={{ fontSize: 12, maxHeight: 220, overflow: "auto" }}>
              {JSON.stringify(scanSnap.strategy_panel, null, 2)}
            </pre>
          </div>
        )}
        {stratSum?.strategies?.length ? (
          <div>
            <h3 style={{ margin: "8px 0 4px" }}>Статистика по стратегиям</h3>
            <table>
              <thead>
                <tr>
                  <th>Стратегия</th>
                  <th>Сделок</th>
                  <th>Winrate</th>
                  <th>PnL USDT</th>
                  <th>Отключена</th>
                </tr>
              </thead>
              <tbody>
                {stratSum.strategies.map((row) => (
                  <tr key={row.strategy_id}>
                    <td>
                      <code>{row.strategy_id}</code>
                    </td>
                    <td>{row.trades}</td>
                    <td>{row.winrate != null ? `${(row.winrate * 100).toFixed(1)}%` : "—"}</td>
                    <td>{row.pnl_usdt}</td>
                    <td>{row.disabled ? "да" : "нет"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p>Нет накопленной статистики по стратегиям (ещё не было закрытых сделок с полем strategy_id).</p>
        )}
      </div>

      <div className="card">
        <h2>Эквити и баланс</h2>
        <p style={{ marginTop: 0, fontSize: 13, opacity: 0.85 }}>
          История снимков эквити/баланса из БД. Полезно видеть дрейф при открытых сделках и после
          закрытий.
        </p>
        <div style={{ width: "100%", height: 320 }}>
          <ResponsiveContainer>
            <LineChart data={chartData}>
              <CartesianGrid stroke="#223055" />
              <XAxis dataKey="t" tick={{ fontSize: 10 }} />
              <YAxis />
              <Tooltip />
              <Legend />
              <Line type="monotone" dataKey="equity" stroke="#5b8cff" dot={false} />
              <Line type="monotone" dataKey="balance" stroke="#49d6a5" dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="card">
        <h2>Закрытые сделки: PnL (до 40 последних)</h2>
        <p style={{ marginTop: 0, fontSize: 13, opacity: 0.85 }}>
          Столбцы по сделкам с заполненным <code>closed_at</code> и <code>pnl_usdt</code>. Ось X —
          условные метки; в подсказке — полный символ и время закрытия.
        </p>
        {closedPnlBars.length === 0 ? (
          <p style={{ opacity: 0.85 }}>Пока нет закрытых сделок с PnL для диаграммы.</p>
        ) : (
          <div style={{ width: "100%", height: 300 }}>
            <ResponsiveContainer>
              <BarChart data={closedPnlBars} margin={{ bottom: 56, left: 4, right: 8 }}>
                <CartesianGrid stroke="#223055" />
                <XAxis dataKey="name" tick={{ fontSize: 9 }} angle={-32} textAnchor="end" height={58} />
                <YAxis tick={{ fontSize: 11 }} />
                <Tooltip
                  formatter={(v) => [`${v} USDT`, "PnL"]}
                  labelFormatter={(_, payload) => {
                    const pl = payload && payload[0] && payload[0].payload;
                    return pl ? `${pl.fullSymbol} · ${pl.t}` : "";
                  }}
                />
                <Bar dataKey="pnl" name="PnL USDT">
                  {closedPnlBars.map((e) => (
                    <Cell key={e.name} fill={e.pnl >= 0 ? "#49d6a5" : "#e74c3c"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      <div className="card" style={{ overflowX: "auto" }}>
        <h2>Открытые позиции</h2>
        <table>
            <thead>
              <tr>
                <th>Символ</th>
                <th>Режим</th>
                <th>Lifecycle</th>
                <th>Сторона</th>
                <th>Вход</th>
                <th>Mark</th>
                <th>Δ к входу</th>
                <th>Нереал. PnL</th>
                <th>До TP %</th>
                <th>До SL %</th>
                <th>SL / TP</th>
                <th>Источник</th>
                <th>Ордер</th>
                <th>Стратегия</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => (
                <tr key={p.id}>
                  <td>{p.symbol}</td>
                  <td>{p.mode}</td>
                  <td>
                    <code>{p.lifecycle_state || "filled"}</code>
                  </td>
                  <td>{p.side}</td>
                  <td>{p.entry_price}</td>
                  <td>{p.last_mark_price != null ? p.last_mark_price : "—"}</td>
                  <td>
                    {p.entry_price != null && p.last_mark_price != null
                      ? fmtSigned(((Number(p.last_mark_price) - Number(p.entry_price)) / Number(p.entry_price)) * 100, 2, "%")
                      : "—"}
                  </td>
                  <td>
                    {p.unrealized_pnl_usdt != null && p.unrealized_pnl_usdt !== undefined ? (
                      <span
                        style={{
                          color: Number(p.unrealized_pnl_usdt) >= 0 ? "#49d6a5" : "#e74c3c",
                          fontWeight: 600,
                        }}
                      >
                        {fmtSigned(Number(p.unrealized_pnl_usdt), 4, " USDT")}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>{p.pct_to_take_profit != null ? `${p.pct_to_take_profit}%` : "—"}</td>
                  <td>{p.pct_to_stop_loss != null ? `${p.pct_to_stop_loss}%` : "—"}</td>
                  <td>
                    {p.stop_loss} / {p.take_profit}
                  </td>
                  <td title="db = только бот; exchange = сверено/с биржи">
                    {p.data_source || "db"}
                  </td>
                  <td style={{ fontSize: 11, maxWidth: 120, wordBreak: "break-all" }}>
                    {p.client_order_id ? `${p.client_order_id.slice(0, 8)}…` : "—"}
                  </td>
                  <td style={{ fontSize: 12 }}>{strategyLabel(p.explanation)}</td>
                </tr>
              ))}
              {positions.length === 0 && (
                <tr>
                  <td colSpan={14}>Нет открытых позиций</td>
                </tr>
              )}
            </tbody>
          </table>
      </div>

      <div className="card">
        <h2>Сделки</h2>
        <table>
          <thead>
            <tr>
              <th>Время</th>
              <th>Символ</th>
              <th>Режим</th>
              <th>Сторона</th>
              <th>PnL</th>
              <th>Статус сделки</th>
              <th>Lifecycle</th>
              <th>Статус ордера</th>
              <th>Источник</th>
              <th>Ордер</th>
              <th>Стратегия</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t) => (
              <tr key={t.id}>
                <td>{fmtTime(t.opened_at)}</td>
                <td>{t.symbol}</td>
                <td>{t.mode}</td>
                <td>{t.side}</td>
                <td>
                  {t.pnl_usdt != null ? `${t.pnl_usdt?.toFixed?.(4) ?? t.pnl_usdt} USDT` : "—"}
                </td>
                <td>{t.status}</td>
                <td>
                  <code>{t.lifecycle_state || "filled"}</code>
                </td>
                <td>{t.order_status || "—"}</td>
                <td>{t.data_source || "db"}</td>
                <td style={{ fontSize: 11, maxWidth: 100, wordBreak: "break-all" }}>
                  {t.client_order_id ? `${t.client_order_id.slice(0, 8)}…` : "—"}
                </td>
                <td style={{ fontSize: 12 }}>{strategyLabel(t.explanation)}</td>
              </tr>
            ))}
            {trades.length === 0 && (
              <tr>
                <td colSpan={11}>Пока нет сделок</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <footer style={{ opacity: 0.6, fontSize: 13, marginTop: 24 }}>
        Vercel: соберите <code>frontend</code> и задеплойте статику; укажите{" "}
        <code>VITE_API_BASE</code> на URL бэкенда. Sentry: <code>VITE_SENTRY_DSN</code>. Firecrawl/Browserbase —
        опционально для новостей/автоматизации (см. README).
      </footer>
    </div>
  );
}
