import { useState, useEffect, useRef } from 'react';
import { createChart, ColorType, CandlestickSeries, LineSeries } from 'lightweight-charts';
import type { IChartApi, Time, IRange } from 'lightweight-charts';
import { 
  ShieldAlert, ShieldCheck, RefreshCw, Layers, TrendingUp, 
  Activity, ArrowUpRight, ArrowDownRight, Award, Wallet, 
  Settings, Clock, Percent, DollarSign, Wifi, WifiOff, Radio
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import './App.css';

// Type Definitions
interface BotStatus {
  is_live: boolean;
  emergency_halt: boolean;
  max_position_size: number;
  max_open_trades: number;
  weekly_drawdown_limit: number;
  loss_cooldown_minutes: number;
  stop_loss_target: number;
  take_profit_target: number;
}

interface PerformanceMetrics {
  total_trades: number;
  closed_trades: number;
  realized_pnl: number;
  win_rate: number;
  profit_factor: string | number;
  gross_profit: number;
  gross_loss: number;
  weekly_pnl: number;
  drawdown_status: string;
}

interface ActivePosition {
  symbol: string;
  quantity: number;
  strategy: string;
  entry_time: number;
  entry_price: number;
  current_price: number;
  cost: number;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  stop_loss: number;
  take_profit: number;
}

interface Trade {
  timestamp: number;
  order_id: string;
  symbol: string;
  strategy: string;
  side: string;
  price: number;
  amount: number;
  cost: number;
  fee: number | null;
  pnl: number | null;
}

interface ChartCandle {
  time: Time;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

interface ChartIndicatorItem {
  time: Time;
  value: number;
}

interface ChartData {
  candles: ChartCandle[];
  indicators: {
    ema_fast: ChartIndicatorItem[];
    ema_slow: ChartIndicatorItem[];
    rsi: ChartIndicatorItem[];
    bb_upper: ChartIndicatorItem[];
    bb_middle: ChartIndicatorItem[];
    bb_lower: ChartIndicatorItem[];
  };
  data_source?: string;
  last_updated?: number | null;
}

interface MarketStatus {
  feed_active: boolean;
  status: string;
  data_source: string;
  last_fetch: number | null;
  symbols_tracked: string[];
  candles_fetched: number;
  error: string | null;
}

export default function App() {
  // Navigation & Control States
  const [selectedPair, setSelectedPair] = useState<string>('BTC/USDT');
  const [refreshRate, setRefreshRate] = useState<string>('30 seconds');
  const [candleCount, setCandleCount] = useState<number>(500);
  const [timeframe, setTimeframe] = useState<string>('15m');
  const [forceTrigger, setForceTrigger] = useState<number>(0);
  const [symbols, setSymbols] = useState<string[]>(['BTC/USDT', 'ETH/USDT']);

  // Overlay Toggles
  const [showEma, setShowEma] = useState<boolean>(true);
  const [showBb, setShowBb] = useState<boolean>(true);
  const [showRsi, setShowRsi] = useState<boolean>(true);

  // Data Fetching States
  const [status, setStatus] = useState<BotStatus | null>(null);
  const [metrics, setMetrics] = useState<PerformanceMetrics | null>(null);
  const [positions, setPositions] = useState<ActivePosition[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [chartData, setChartData] = useState<ChartData | null>(null);
  const [marketStatus, setMarketStatus] = useState<MarketStatus | null>(null);
  
  const [error, setError] = useState<string | null>(null);

  // Interactive settings state
  const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' | 'info' } | null>(null);
  const [isEditing, setIsEditing] = useState<boolean>(false);
  const [editValues, setEditValues] = useState({
    max_position_size: 0,
    max_open_trades: 0,
    weekly_drawdown_limit: 0,
    loss_cooldown_minutes: 0
  });

  const showToastMessage = (message: string, type: 'success' | 'error' | 'info' = 'success') => {
    setToast({ message, type });
  };

  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(null), 4000);
      return () => clearTimeout(timer);
    }
  }, [toast]);

  const fetchSymbols = async () => {
    try {
      const res = await fetch('/api/symbols');
      const data = await res.json();
      if (Array.isArray(data) && data.length > 0) {
        setSymbols(data);
        if (!data.includes(selectedPair)) {
          setSelectedPair(data[0]);
        }
      }
    } catch (e) {
      console.error('Error fetching symbols:', e);
    }
  };

  const handleToggleExecutionMode = async () => {
    if (!status) return;
    const newIsLive = !status.is_live;
    try {
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_live: newIsLive })
      });
      const data = await res.json();
      if (data.status === 'success') {
        setStatus(data.settings);
        showToastMessage(`Switched execution mode to ${newIsLive ? 'LIVE TRADING' : 'SANDBOX MOCK'}`, 'success');
      } else {
        showToastMessage(data.message || 'Failed to update execution mode', 'error');
      }
    } catch (e) {
      console.error(e);
      showToastMessage('Failed to update execution mode due to network error', 'error');
    }
  };

  const handleToggleEmergencyHalt = async () => {
    if (!status) return;
    const newHalt = !status.emergency_halt;
    try {
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ emergency_halt: newHalt })
      });
      const data = await res.json();
      if (data.status === 'success') {
        setStatus(data.settings);
        showToastMessage(newHalt ? 'EMERGENCY HALT ACTIVATED!' : 'Emergency halt deactivated.', newHalt ? 'error' : 'success');
      } else {
        showToastMessage(data.message || 'Failed to toggle emergency halt', 'error');
      }
    } catch (e) {
      console.error(e);
      showToastMessage('Failed to toggle emergency halt due to network error', 'error');
    }
  };

  const startEditing = () => {
    if (!status) return;
    setEditValues({
      max_position_size: status.max_position_size,
      max_open_trades: status.max_open_trades,
      weekly_drawdown_limit: status.weekly_drawdown_limit,
      loss_cooldown_minutes: status.loss_cooldown_minutes
    });
    setIsEditing(true);
  };

  const cancelEditing = () => {
    setIsEditing(false);
  };

  const saveSettings = async () => {
    try {
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(editValues)
      });
      const data = await res.json();
      if (data.status === 'success') {
        setStatus(data.settings);
        setIsEditing(false);
        showToastMessage('Risk guardrail settings updated successfully', 'success');
      } else {
        showToastMessage(data.message || 'Failed to update settings', 'error');
      }
    } catch (e) {
      console.error(e);
      showToastMessage('Network error while saving settings', 'error');
    }
  };

  useEffect(() => {
    fetchSymbols();
  }, []);

  // Chart References
  const mainChartContainerRef = useRef<HTMLDivElement>(null);
  const rsiChartContainerRef = useRef<HTMLDivElement>(null);
  const mainChartRef = useRef<IChartApi | null>(null);
  const rsiChartRef = useRef<IChartApi | null>(null);

  // Fetch All Telemetry Data
  const fetchTelemetry = async () => {
    try {
      setError(null);
      const [statusRes, metricsRes, positionsRes, tradesRes, marketStatusRes] = await Promise.all([
        fetch('/api/status').then(r => r.json()),
        fetch('/api/metrics').then(r => r.json()),
        fetch('/api/positions').then(r => r.json()),
        fetch('/api/trades').then(r => r.json()),
        fetch('/api/market-status').then(r => r.json())
      ]);

      setStatus(statusRes);
      setMetrics(metricsRes);
      setPositions(positionsRes);
      setTrades(tradesRes);
      setMarketStatus(marketStatusRes);
    } catch (e) {
      console.error('Error fetching bot telemetry data:', e);
      setError('Failed to fetch bot telemetry data. Please ensure the backend API server is running.');
    }
  };

  // Fetch Candlestick & Indicators Chart Data
  const fetchChart = async () => {
    try {
      const chartRes = await fetch(`/api/chart?symbol=${encodeURIComponent(selectedPair)}&timeframe=${timeframe}&limit=${candleCount}`).then(r => r.json());
      setChartData(chartRes);
    } catch (e) {
      console.error('Error fetching candlestick chart data:', e);
    }
  };

  // Initialize Polling Interval
  useEffect(() => {
    fetchTelemetry();
    fetchChart();

    if (refreshRate === 'Manual') return;
    
    const intervalSecs = refreshRate === '10 seconds' ? 10 : refreshRate === '30 seconds' ? 30 : 60;
    const timer = setInterval(() => {
      fetchTelemetry();
      fetchChart();
    }, intervalSecs * 1000);

    return () => clearInterval(timer);
  }, [refreshRate, selectedPair, candleCount, timeframe, forceTrigger]);

  // Handle Chart Creation & Updates
  useEffect(() => {
    if (!chartData || !chartData.candles || chartData.candles.length === 0) return;
    if (!mainChartContainerRef.current) return;

    // Clean up existing charts
    if (mainChartRef.current) {
      mainChartRef.current.remove();
      mainChartRef.current = null;
    }
    if (rsiChartRef.current) {
      rsiChartRef.current.remove();
      rsiChartRef.current = null;
    }

    const containerWidth = mainChartContainerRef.current.clientWidth;

    const istTickMarkFormatter = (time: Time, tickMarkType: number, locale: string) => {
      if (typeof time !== 'number') return String(time);
      const date = new Date(time * 1000);
      const formatOptions: Intl.DateTimeFormatOptions = {
        timeZone: 'Asia/Kolkata',
      };

      switch (tickMarkType) {
        case 0: // Year
          formatOptions.year = 'numeric';
          break;
        case 1: // Month
          formatOptions.month = 'short';
          break;
        case 2: // DayOfMonth
          formatOptions.day = 'numeric';
          break;
        case 3: // Time
          formatOptions.hour = '2-digit';
          formatOptions.minute = '2-digit';
          formatOptions.hour12 = false;
          break;
        case 4: // TimeWithSeconds
          formatOptions.hour = '2-digit';
          formatOptions.minute = '2-digit';
          formatOptions.second = '2-digit';
          formatOptions.hour12 = false;
          break;
        default:
          formatOptions.hour = '2-digit';
          formatOptions.minute = '2-digit';
          formatOptions.hour12 = false;
      }
      return new Intl.DateTimeFormat(locale, formatOptions).format(date);
    };

    // Create Main Candlestick Chart
    const mainChart = createChart(mainChartContainerRef.current, {
      width: containerWidth,
      height: 380,
      layout: {
        background: { type: ColorType.Solid, color: '#11141a' },
        textColor: '#8b949e',
        fontSize: 11,
      },
      localization: {
        locale: 'en-IN',
        timeFormatter: (timestamp: number) => {
          return new Date(timestamp * 1000).toLocaleString('en-US', {
            timeZone: 'Asia/Kolkata',
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: false
          }) + ' IST';
        }
      },
      grid: {
        vertLines: { color: 'rgba(255, 255, 255, 0.02)' },
        horzLines: { color: 'rgba(255, 255, 255, 0.02)' },
      },
      rightPriceScale: {
        borderColor: 'rgba(255, 255, 255, 0.06)',
      },
      timeScale: {
        borderColor: 'rgba(255, 255, 255, 0.06)',
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: istTickMarkFormatter,
      },
    });
    mainChartRef.current = mainChart;

    const candlestickSeries = mainChart.addSeries(CandlestickSeries, {
      upColor: '#26a69a',
      downColor: '#ef5350',
      borderVisible: false,
      wickUpColor: '#26a69a',
      wickDownColor: '#ef5350',
    });
    candlestickSeries.setData(chartData.candles);

    // Add Indicators
    let emaFastSeries: any = null;
    let emaSlowSeries: any = null;
    if (showEma) {
      emaFastSeries = mainChart.addSeries(LineSeries, {
        color: '#ffc107',
        lineWidth: 2,
        title: 'EMA 9',
      });
      emaFastSeries.setData(chartData.indicators.ema_fast);

      emaSlowSeries = mainChart.addSeries(LineSeries, {
        color: '#e83e8c',
        lineWidth: 2,
        title: 'EMA 21',
      });
      emaSlowSeries.setData(chartData.indicators.ema_slow);
    }

    let bbUpperSeries: any = null;
    let bbMiddleSeries: any = null;
    let bbLowerSeries: any = null;
    if (showBb) {
      bbUpperSeries = mainChart.addSeries(LineSeries, {
        color: '#17a2b8',
        lineWidth: 1,
        lineStyle: 1, // Dashed
        title: 'BB Upper',
      });
      bbUpperSeries.setData(chartData.indicators.bb_upper);

      bbMiddleSeries = mainChart.addSeries(LineSeries, {
        color: 'rgba(23, 162, 184, 0.5)',
        lineWidth: 1,
        lineStyle: 1, // Dashed
        title: 'BB Middle',
      });
      bbMiddleSeries.setData(chartData.indicators.bb_middle);

      bbLowerSeries = mainChart.addSeries(LineSeries, {
        color: '#17a2b8',
        lineWidth: 1,
        lineStyle: 1, // Dashed
        title: 'BB Lower',
      });
      bbLowerSeries.setData(chartData.indicators.bb_lower);
    }

    // Create secondary RSI Chart
    let rsiChart: any = null;
    if (showRsi && rsiChartContainerRef.current) {
      rsiChart = createChart(rsiChartContainerRef.current, {
        width: containerWidth,
        height: 120,
        layout: {
          background: { type: ColorType.Solid, color: '#11141a' },
          textColor: '#8b949e',
          fontSize: 11,
        },
        localization: {
          locale: 'en-IN',
          timeFormatter: (timestamp: number) => {
            return new Date(timestamp * 1000).toLocaleString('en-US', {
              timeZone: 'Asia/Kolkata',
              year: 'numeric',
              month: 'short',
              day: 'numeric',
              hour: '2-digit',
              minute: '2-digit',
              second: '2-digit',
              hour12: false
            }) + ' IST';
          }
        },
        grid: {
          vertLines: { color: 'rgba(255, 255, 255, 0.02)' },
          horzLines: { color: 'rgba(255, 255, 255, 0.02)' },
        },
        rightPriceScale: {
          borderColor: 'rgba(255, 255, 255, 0.06)',
          visible: true,
        },
        timeScale: {
          borderColor: 'rgba(255, 255, 255, 0.06)',
          timeVisible: true,
          tickMarkFormatter: istTickMarkFormatter,
        },
      });
      rsiChartRef.current = rsiChart;

      const rsiSeries = rsiChart.addSeries(LineSeries, {
        color: '#007bff',
        lineWidth: 2,
        title: 'RSI 14',
      });
      rsiSeries.setData(chartData.indicators.rsi);

      // Oversold/Overbought levels (RSI baseline annotations)
      const rsiLimitUpper = rsiChart.addSeries(LineSeries, {
        color: 'rgba(248, 81, 73, 0.4)',
        lineWidth: 1,
        lineStyle: 2, // Dotted
      });
      rsiLimitUpper.setData(chartData.candles.map(c => ({ time: c.time, value: 70 })));

      const rsiLimitLower = rsiChart.addSeries(LineSeries, {
        color: 'rgba(63, 185, 80, 0.4)',
        lineWidth: 1,
        lineStyle: 2, // Dotted
      });
      rsiLimitLower.setData(chartData.candles.map(c => ({ time: c.time, value: 30 })));

      // Synchronize visible timescale range
      mainChart.timeScale().subscribeVisibleTimeRangeChange((range: IRange<Time> | null) => {
        if (range) {
          rsiChart.timeScale().setVisibleRange(range);
        }
      });
      rsiChart.timeScale().subscribeVisibleTimeRangeChange((range: IRange<Time> | null) => {
        if (range) {
          mainChart.timeScale().setVisibleRange(range);
        }
      });
    }

    // Handle window resizing
    const handleResize = () => {
      if (mainChartContainerRef.current) {
        const w = mainChartContainerRef.current.clientWidth;
        mainChart.resize(w, 380);
        if (rsiChart) rsiChart.resize(w, 120);
      }
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      mainChart.remove();
      mainChartRef.current = null;
      if (rsiChart) {
        rsiChart.remove();
        rsiChartRef.current = null;
      }
    };
  }, [chartData, showEma, showBb, showRsi]);

  const handleForceRefresh = () => {
    setForceTrigger(prev => prev + 1);
  };

  const formatDate = (timestampMs: number) => {
    return new Date(timestampMs).toLocaleString('en-US', {
      timeZone: 'Asia/Kolkata',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false
    }) + ' IST';
  };

  return (
    <>
      {/* ------------------ SIDEBAR NAVIGATION ------------------ */}
      <aside className="sidebar" id="sidebar-controls">
        <div className="brand-section">
          <span className="brand-logo" role="img" aria-label="OneGuard Logo">🛡️</span>
          <span className="brand-name">OneGuard Command</span>
        </div>

        {/* System Configuration Badge Stack */}
        <div className="sidebar-group">
          <h3 className="sidebar-title">System Configuration</h3>
          
          <div className="status-badge clickable" id="execution-mode-badge" onClick={handleToggleExecutionMode} title="Click to toggle execution mode">
            <span className="badge-label">Execution Mode</span>
            {status ? (
              <span className={`badge-value ${status.is_live ? 'red' : 'orange'}`}>
                <span className="pulse-dot"></span>
                {status.is_live ? 'LIVE TRADING' : 'SANDBOX MOCK'}
              </span>
            ) : (
              <span className="badge-value">LOADING</span>
            )}
          </div>

          <div className="status-badge clickable" id="emergency-halt-badge" onClick={handleToggleEmergencyHalt} title="Click to toggle Emergency Halt">
            <span className="badge-label">Emergency Guard</span>
            {status ? (
              <span className={`badge-value ${status.emergency_halt ? 'red' : 'green'}`}>
                {status.emergency_halt ? <ShieldAlert size={14} /> : <ShieldCheck size={14} />}
                {status.emergency_halt ? 'HALTED' : 'RUNNING'}
              </span>
            ) : (
              <span className="badge-value">LOADING</span>
            )}
          </div>
        </div>

        {/* Controls Segment */}
        <div className="sidebar-group">
          <h3 className="sidebar-title">Dashboard Controls</h3>
          
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <label className="badge-label" htmlFor="refresh-rate-select">Auto Refresh Rate</label>
            <select 
              id="refresh-rate-select" 
              className="select-input" 
              value={refreshRate}
              onChange={(e) => setRefreshRate(e.target.value)}
            >
              <option value="Manual">Manual</option>
              <option value="10 seconds">10 seconds</option>
              <option value="30 seconds">30 seconds</option>
              <option value="60 seconds">60 seconds</option>
            </select>
          </div>

          <button 
            type="button" 
            id="force-refresh-btn" 
            className="action-btn"
            onClick={handleForceRefresh}
          >
            <RefreshCw size={14} /> Force Refresh
          </button>
        </div>

        {/* Risk Guardrails settings display */}
        <div className="sidebar-group" style={{ marginTop: 'auto' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h3 className="sidebar-title" style={{ marginBottom: 0 }}>Risk Guardrails</h3>
            {!isEditing && status && (
              <button 
                onClick={startEditing} 
                style={{ 
                  background: 'none', 
                  border: 'none', 
                  color: 'var(--color-accent)', 
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                  fontSize: '12px',
                  fontWeight: 600
                }}
              >
                <Settings size={12} /> Edit
              </button>
            )}
          </div>
          <div className="risk-list" id="risk-guardrails-list">
            <div className="risk-item">
              <span className="risk-label">Max Position Size</span>
              {isEditing ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                  <input
                    type="number"
                    className="risk-input"
                    value={editValues.max_position_size}
                    onChange={(e) => setEditValues({ ...editValues, max_position_size: parseFloat(e.target.value) || 0 })}
                  />
                  <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>USDT</span>
                </div>
              ) : (
                <span className="risk-val">{status ? `${status.max_position_size} USDT` : '-'}</span>
              )}
            </div>
            <div className="risk-item">
              <span className="risk-label">Max Open Trades</span>
              {isEditing ? (
                <input
                  type="number"
                  className="risk-input"
                  value={editValues.max_open_trades}
                  onChange={(e) => setEditValues({ ...editValues, max_open_trades: parseInt(e.target.value) || 0 })}
                />
              ) : (
                <span className="risk-val">{status ? status.max_open_trades : '-'}</span>
              )}
            </div>
            <div className="risk-item">
              <span className="risk-label">Weekly PnL Limit</span>
              {isEditing ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                  <input
                    type="number"
                    className="risk-input"
                    value={editValues.weekly_drawdown_limit}
                    onChange={(e) => setEditValues({ ...editValues, weekly_drawdown_limit: parseFloat(e.target.value) || 0 })}
                  />
                  <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>USDT</span>
                </div>
              ) : (
                <span className="risk-val">{status ? `${status.weekly_drawdown_limit} USDT` : '-'}</span>
              )}
            </div>
            <div className="risk-item">
              <span className="risk-label">Loss Cooldown</span>
              {isEditing ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                  <input
                    type="number"
                    className="risk-input"
                    value={editValues.loss_cooldown_minutes}
                    onChange={(e) => setEditValues({ ...editValues, loss_cooldown_minutes: parseInt(e.target.value) || 0 })}
                  />
                  <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>m</span>
                </div>
              ) : (
                <span className="risk-val">{status ? `${status.loss_cooldown_minutes}m` : '-'}</span>
              )}
            </div>
            <div className="risk-item">
              <span className="risk-label">Stop Loss Target</span>
              <span className="risk-val" style={{ opacity: 0.6 }}>{status ? `${status.stop_loss_target}%` : '-'}</span>
            </div>
            <div className="risk-item">
              <span className="risk-label">Take Profit Target</span>
              <span className="risk-val" style={{ opacity: 0.6 }}>{status ? `${status.take_profit_target}%` : '-'}</span>
            </div>
          </div>
          {isEditing && (
            <div style={{ display: 'flex', gap: '8px', marginTop: '8px' }}>
              <button 
                onClick={saveSettings}
                className="action-btn"
                style={{ 
                  flex: 1, 
                  padding: '6px 12px', 
                  fontSize: '12px', 
                  background: 'rgba(63, 185, 80, 0.1)',
                  borderColor: 'rgba(63, 185, 80, 0.3)',
                  color: 'var(--color-green)'
                }}
              >
                Save
              </button>
              <button 
                onClick={cancelEditing}
                className="action-btn"
                style={{ 
                  flex: 1, 
                  padding: '6px 12px', 
                  fontSize: '12px', 
                  background: 'rgba(248, 81, 73, 0.1)',
                  borderColor: 'rgba(248, 81, 73, 0.3)',
                  color: 'var(--color-red)'
                }}
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      </aside>

      {/* ------------------ MAIN DASHBOARD AREA ------------------ */}
      <main className="main-dashboard" id="main-telemetry">
        
        {/* Header Title Section */}
        <header className="header-panel">
          <h1 className="dashboard-title">🛡️ OneGuard Bot | Engineering Command Center</h1>
          <p className="dashboard-subtitle">Real-time Bot Performance, Risk Guardrails, and Indicator Pipeline</p>
        </header>

        {/* Error alerting if API fails */}
        {error && (
          <div className="error-state" id="telemetry-error-alert">
            <ShieldAlert size={18} />
            <span>{error}</span>
          </div>
        )}

        {/* KPI Strip */}
        <div className="kpi-strip" id="kpi-strip-container">
          {/* Total Realized PnL Card */}
          <motion.div 
            className="panel kpi-card" 
            id="kpi-realized-pnl"
            initial={{ opacity: 0, y: 15 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3 }}
          >
            <div className="kpi-header">
              <span className="kpi-title">Total Realized PnL</span>
              <Wallet size={16} className="kpi-icon" />
            </div>
            <span className={`kpi-val ${metrics ? (metrics.realized_pnl >= 0 ? 'positive' : 'negative') : ''}`}>
              {metrics ? `${metrics.realized_pnl >= 0 ? '+' : ''}${metrics.realized_pnl.toFixed(2)} USDT` : '0.00 USDT'}
            </span>
            <div className={`kpi-delta ${metrics ? (metrics.realized_pnl >= 0 ? 'up' : 'down') : ''}`}>
              {metrics && metrics.realized_pnl !== 0 ? (
                <>
                  {metrics.realized_pnl >= 0 ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />}
                  <span>{metrics.realized_pnl.toFixed(2)} USDT</span>
                </>
              ) : (
                <span>No realized profit</span>
              )}
            </div>
          </motion.div>

          {/* Win Rate Card */}
          <motion.div 
            className="panel kpi-card" 
            id="kpi-win-rate"
            initial={{ opacity: 0, y: 15 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, delay: 0.05 }}
          >
            <div className="kpi-header">
              <span className="kpi-title">Trade Win Rate</span>
              <Percent size={16} className="kpi-icon" />
            </div>
            <span className="kpi-val">
              {metrics ? `${metrics.win_rate.toFixed(1)}%` : '0.0%'}
            </span>
            <div className="kpi-delta">
              <Award size={14} />
              <span>{metrics ? metrics.closed_trades : 0} Closed Trades</span>
            </div>
          </motion.div>

          {/* Profit Factor Card */}
          <motion.div 
            className="panel kpi-card" 
            id="kpi-profit-factor"
            initial={{ opacity: 0, y: 15 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, delay: 0.1 }}
          >
            <div className="kpi-header">
              <span className="kpi-title">Profit Factor</span>
              <TrendingUp size={16} className="kpi-icon" />
            </div>
            <span className="kpi-val">
              {metrics ? (metrics.profit_factor === 'inf' ? '∞' : metrics.profit_factor) : 'N/A'}
            </span>
            <div className="kpi-delta">
              <DollarSign size={14} />
              <span>Gross Profit: {metrics ? metrics.gross_profit.toFixed(1) : '0.0'}</span>
            </div>
          </motion.div>

          {/* Weekly PnL Card */}
          <motion.div 
            className="panel kpi-card" 
            id="kpi-weekly-pnl"
            initial={{ opacity: 0, y: 15 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, delay: 0.15 }}
          >
            <div className="kpi-header">
              <span className="kpi-title">Weekly Realized PnL</span>
              <Activity size={16} className="kpi-icon" />
            </div>
            <span className="kpi-val">
              {metrics ? `${metrics.weekly_pnl.toFixed(2)} USDT` : '0.00 USDT'}
            </span>
            <div className={`kpi-delta ${metrics && metrics.drawdown_status === 'SAFE' ? 'up' : 'down'}`}>
              <Settings size={14} />
              <span>Status: {metrics ? metrics.drawdown_status : 'SAFE'}</span>
            </div>
          </motion.div>

          {/* Active Positions Card */}
          <motion.div 
            className="panel kpi-card" 
            id="kpi-active-positions"
            initial={{ opacity: 0, y: 15 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, delay: 0.2 }}
          >
            <div className="kpi-header">
              <span className="kpi-title">Active Positions</span>
              <Layers size={16} className="kpi-icon" />
            </div>
            <span className="kpi-val">
              {positions.length} / {status ? status.max_open_trades : 3}
            </span>
            <div className="kpi-delta">
              <Clock size={14} />
              <span>Total Trades: {metrics ? metrics.total_trades : 0}</span>
            </div>
          </motion.div>
        </div>

        {/* ------------------ ACTIVE POSITIONS LIVE TRACKER ------------------ */}
        <section className="section-container" id="active-positions-section">
          <div className="section-header">
            <h2 className="section-title">
              <span className="section-icon">💼</span> Active Positions & Live Valuation
            </h2>
          </div>

          <div className="panel" style={{ overflow: 'hidden' }}>
            {positions.length === 0 ? (
              <div className="info-state" id="no-active-positions-msg">
                <span>No active positions currently held by the bot.</span>
              </div>
            ) : (
              <div className="table-wrapper">
                <table className="data-table" id="active-positions-table">
                  <thead>
                    <tr>
                      <th>Symbol</th>
                      <th>Quantity</th>
                      <th>Strategy</th>
                      <th>Entry Time</th>
                      <th>Entry Price</th>
                      <th>Current Price</th>
                      <th>Total Cost</th>
                      <th>Market Value</th>
                      <th>Unrealized PnL</th>
                      <th>Unrealized PnL (%)</th>
                      <th>Stop Loss</th>
                      <th>Take Profit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positions.map((pos) => {
                      const isPnLPositive = pos.unrealized_pnl >= 0;
                      return (
                        <tr key={pos.symbol}>
                          <td className="mono-cell" style={{ fontWeight: 600 }}>{pos.symbol}</td>
                          <td className="mono-cell">{pos.quantity.toFixed(6)}</td>
                          <td>
                            <span className="badge-inline buy">{pos.strategy}</span>
                          </td>
                          <td style={{ fontSize: '12px' }}>{formatDate(pos.entry_time)}</td>
                          <td className="mono-cell">{pos.entry_price.toFixed(4)} USDT</td>
                          <td className="mono-cell">{pos.current_price.toFixed(4)} USDT</td>
                          <td className="mono-cell">{pos.cost.toFixed(2)} USDT</td>
                          <td className="mono-cell">{pos.market_value.toFixed(2)} USDT</td>
                          <td className={`mono-cell ${isPnLPositive ? 'pnl-green' : 'pnl-red'}`} style={{ fontWeight: 600 }}>
                            {isPnLPositive ? '+' : ''}{pos.unrealized_pnl.toFixed(2)} USDT
                          </td>
                          <td className={`mono-cell ${isPnLPositive ? 'pnl-green' : 'pnl-red'}`} style={{ fontWeight: 600 }}>
                            {isPnLPositive ? '+' : ''}{pos.unrealized_pnl_pct.toFixed(2)}%
                          </td>
                          <td className="mono-cell" style={{ color: 'var(--color-red)' }}>{pos.stop_loss.toFixed(4)} USDT</td>
                          <td className="mono-cell" style={{ color: 'var(--color-green)' }}>{pos.take_profit.toFixed(4)} USDT</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>

        {/* ------------------ INTERACTIVE MARKET ANALYSIS CHARTS ------------------ */}
        <section className="section-container" id="market-analysis-section">
          <div className="section-header">
            <h2 className="section-title">
              <span className="section-icon">📈</span> Interactive Market Analysis & Technical Pipeline
            </h2>
            <div className="market-feed-status">
              {marketStatus ? (
                marketStatus.feed_active ? (
                  <div className="feed-badge feed-live">
                    <span className="feed-pulse-dot"></span>
                    <Wifi size={13} />
                    <span>LIVE</span>
                  </div>
                ) : marketStatus.status === 'starting' ? (
                  <div className="feed-badge feed-connecting">
                    <Radio size={13} />
                    <span>CONNECTING...</span>
                  </div>
                ) : (
                  <div className="feed-badge feed-error" title={marketStatus.error || 'Feed error'}>
                    <WifiOff size={13} />
                    <span>FEED ERROR</span>
                  </div>
                )
              ) : (
                <div className="feed-badge feed-connecting">
                  <Radio size={13} />
                  <span>CONNECTING...</span>
                </div>
              )}
              {marketStatus?.last_fetch && (
                <span className="feed-freshness">
                  Updated {Math.round((Date.now() / 1000) - marketStatus.last_fetch)}s ago
                </span>
              )}
            </div>
          </div>

          <div className="panel chart-container-panel" id="tradingview-chart-panel">
            {/* Chart Control Header Strip */}
            <div className="chart-controls">
              <div className="chart-controls-left">
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <label className="badge-label" htmlFor="trading-pair-select" style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Trading Pair</label>
                  <select 
                    id="trading-pair-select" 
                    className="select-input" 
                    style={{ width: '130px', padding: '6px 12px', fontSize: '12px' }}
                    value={selectedPair}
                    onChange={(e) => setSelectedPair(e.target.value)}
                  >
                    {symbols.map(sym => (
                      <option key={sym} value={sym}>{sym}</option>
                    ))}
                  </select>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <label className="badge-label" htmlFor="timeframe-select" style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Timeframe</label>
                  <select 
                    id="timeframe-select" 
                    className="select-input" 
                    style={{ width: '90px', padding: '6px 12px', fontSize: '12px' }}
                    value={timeframe}
                    onChange={(e) => setTimeframe(e.target.value)}
                  >
                    <option value="1m">1 min</option>
                    <option value="5m">5 min</option>
                    <option value="15m">15 min</option>
                    <option value="30m">30 min</option>
                    <option value="1h">1 hour</option>
                    <option value="4h">4 hours</option>
                    <option value="1d">1 day</option>
                  </select>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <label className="badge-label" htmlFor="candle-count-select" style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Candles</label>
                  <select 
                    id="candle-count-select" 
                    className="select-input" 
                    style={{ width: '90px', padding: '6px 12px', fontSize: '12px' }}
                    value={candleCount}
                    onChange={(e) => setCandleCount(Number(e.target.value))}
                  >
                    <option value={30}>30</option>
                    <option value={50}>50</option>
                    <option value={100}>100</option>
                    <option value={150}>150</option>
                    <option value={200}>200</option>
                    <option value={500}>500</option>
                    <option value={1000}>1000</option>
                  </select>
                </div>
              </div>

              {/* Indicator Toggles */}
              <div className="chart-controls-right">
                <span className="badge-label" style={{ fontSize: '11px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Overlays</span>
                
                <div className="toggle-group" id="chart-overlays-toggle-group">
                  <button 
                    type="button"
                    className={`toggle-btn ${showEma ? 'active' : ''}`}
                    onClick={() => setShowEma(!showEma)}
                  >
                    EMA
                  </button>
                  <button 
                    type="button"
                    className={`toggle-btn ${showBb ? 'active' : ''}`}
                    onClick={() => setShowBb(!showBb)}
                  >
                    Bollinger
                  </button>
                  <button 
                    type="button"
                    className={`toggle-btn ${showRsi ? 'active' : ''}`}
                    onClick={() => setShowRsi(!showRsi)}
                  >
                    RSI Pane
                  </button>
                </div>
              </div>
            </div>

            {/* TradingView Lightweight Charts Rendering Canvas */}
            <div className="chart-panes">
              <div className="primary-chart-wrapper">
                {/* Floating legend labels */}
                <div className="chart-legend">
                  <div className="legend-item">
                    <span className="legend-dot" style={{ backgroundColor: '#26a69a' }}></span>
                    <span>Candlesticks</span>
                  </div>
                  {showEma && (
                    <>
                      <div className="legend-item">
                        <span className="legend-dot" style={{ backgroundColor: '#ffc107' }}></span>
                        <span>EMA 9</span>
                      </div>
                      <div className="legend-item">
                        <span className="legend-dot" style={{ backgroundColor: '#e83e8c' }}></span>
                        <span>EMA 21</span>
                      </div>
                    </>
                  )}
                  {showBb && (
                    <div className="legend-item">
                      <span className="legend-dot" style={{ backgroundColor: '#17a2b8' }}></span>
                      <span>Bollinger Bands</span>
                    </div>
                  )}
                </div>
                
                <div ref={mainChartContainerRef} style={{ width: '100%', height: '100%' }}></div>
                
                {!chartData && (
                  <div className="info-state" style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', backgroundColor: 'rgba(10,12,16,0.85)' }}>
                    <span>Loading market candlestick history...</span>
                  </div>
                )}
                {chartData && chartData.candles.length === 0 && (
                  <div className="info-state" style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', backgroundColor: 'rgba(10,12,16,0.85)' }}>
                    <span>No candle data found for {selectedPair} in local database.</span>
                  </div>
                )}
              </div>

              {showRsi && (
                <div className="rsi-chart-wrapper">
                  <div className="chart-legend">
                    <div className="legend-item">
                      <span className="legend-dot" style={{ backgroundColor: '#007bff' }}></span>
                      <span>RSI 14</span>
                    </div>
                  </div>
                  <div ref={rsiChartContainerRef} style={{ width: '100%', height: '100%' }}></div>
                </div>
              )}
            </div>
          </div>
        </section>

        {/* ------------------ HISTORICAL TRADES LEDGER ------------------ */}
        <section className="section-container" id="historical-trades-section">
          <div className="section-header">
            <h2 className="section-title">
              <span className="section-icon">📜</span> Historical Trades Ledger
            </h2>
          </div>

          <div className="panel" style={{ overflow: 'hidden' }}>
            {trades.length === 0 ? (
              <div className="info-state" id="no-trades-logged-msg">
                <span>No trade records found in database history.</span>
              </div>
            ) : (
              <div className="table-wrapper">
                <table className="data-table" id="trades-history-table">
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Order ID</th>
                      <th>Symbol</th>
                      <th>Strategy</th>
                      <th>Side</th>
                      <th>Price</th>
                      <th>Amount</th>
                      <th>Cost</th>
                      <th>Fee</th>
                      <th>PnL</th>
                    </tr>
                  </thead>
                  <tbody>
                    <AnimatePresence>
                      {trades.map((trade, idx) => {
                        const isBuy = trade.side.toUpperCase() === 'BUY';
                        const isPnLPositive = trade.pnl !== null && trade.pnl >= 0;
                        return (
                          <motion.tr 
                            key={trade.order_id}
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            transition={{ duration: 0.2, delay: Math.min(idx * 0.01, 0.3) }}
                          >
                            <td style={{ fontSize: '12px' }}>{formatDate(trade.timestamp)}</td>
                            <td className="mono-cell" style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>{trade.order_id}</td>
                            <td className="mono-cell" style={{ fontWeight: 600 }}>{trade.symbol}</td>
                            <td>{trade.strategy}</td>
                            <td>
                              <span className={`badge-inline ${isBuy ? 'buy' : 'sell'}`}>{trade.side}</span>
                            </td>
                            <td className="mono-cell">{trade.price.toFixed(4)} USDT</td>
                            <td className="mono-cell">{trade.amount.toFixed(6)}</td>
                            <td className="mono-cell">{trade.cost.toFixed(2)} USDT</td>
                            <td className="mono-cell">{trade.fee !== null ? `${trade.fee.toFixed(4)} USDT` : '-'}</td>
                            <td className={`mono-cell ${trade.pnl !== null ? (isPnLPositive ? 'pnl-green' : 'pnl-red') : ''}`} style={{ fontWeight: 600 }}>
                              {trade.pnl !== null ? `${isPnLPositive ? '+' : ''}${trade.pnl.toFixed(2)} USDT` : '-'}
                            </td>
                          </motion.tr>
                        );
                      })}
                    </AnimatePresence>
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>
        
      </main>

      <AnimatePresence>
        {toast && (
          <motion.div
            className={`toast toast-${toast.type}`}
            initial={{ opacity: 0, y: 50, scale: 0.9 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 20, scale: 0.9 }}
            transition={{ duration: 0.2 }}
            style={{
              position: 'fixed',
              bottom: '24px',
              right: '24px',
              zIndex: 9999,
              padding: '12px 20px',
              borderRadius: '8px',
              background: toast.type === 'success' 
                ? 'rgba(63, 185, 80, 0.95)' 
                : toast.type === 'error' 
                ? 'rgba(248, 81, 73, 0.95)' 
                : 'rgba(88, 166, 255, 0.95)',
              color: '#fff',
              fontWeight: 600,
              fontSize: '14px',
              boxShadow: '0 8px 32px 0 rgba(0, 0, 0, 0.5)',
              backdropFilter: 'blur(8px)',
              border: '1px solid rgba(255, 255, 255, 0.1)',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
            }}
          >
            {toast.type === 'success' && <ShieldCheck size={16} />}
            {toast.type === 'error' && <ShieldAlert size={16} />}
            {toast.type === 'info' && <RefreshCw size={16} />}
            {toast.message}
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
