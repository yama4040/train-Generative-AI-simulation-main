import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { GIFEncoder, applyPalette, quantize } from 'gifenc';
import Canvas from './Canvas';
import RouteEditorCanvas from './RouteEditorCanvas';
import TrainEditor from './TrainEditor';

const NETWORK_STORAGE_KEY = 'route_network_v2';

const DEFAULT_VEHICLE_PARAMS = {
  max_speed: 60,
  length: 200,
  weight: 30.0,             // 追加: 既定の車両重量(t)
  factor_of_inertia: 1.1,   // 追加: 既定の慣性係数
  accel: 3.2,
  decel: 4.0,
  low_precision_accel: 3.0,
  low_precision_decel: 4.0,
  safe_gap: 20.0,
  min_follow_speed: 20.0,
  accel_sign_cooldown: 5.0,
  idm_delta: 4.0
};

const VEHICLE_PARAM_FIELDS = [
  { key: 'max_speed', label: '既定最大速度', unit: 'km/h', min: 0.1, step: 0.1 },
  { key: 'length', label: '既定列車長', unit: 'm', min: 0.1, step: 0.1 },
  { key: 'weight', label: '既定車両重量', unit: 't', min: 0.1, step: 0.1 },
  { key: 'factor_of_inertia', label: '既定慣性係数', unit: '', min: 1.0, step: 0.1 },
  { key: 'accel', label: '既定加速度', unit: 'km/h/s', min: 0.1, step: 0.1 },
  { key: 'decel', label: '既定減速度', unit: 'km/h/s', min: 0.1, step: 0.1 },
  { key: 'low_precision_accel', label: '低精度モード加速度', unit: 'km/h/s', min: 0.1, step: 0.1 },
  { key: 'low_precision_decel', label: '低精度モード減速度', unit: 'km/h/s', min: 0.1, step: 0.1 },
  { key: 'safe_gap', label: '安全余裕距離', unit: 'm', min: 0, step: 1 },
  { key: 'min_follow_speed', label: '追従時最低速度', unit: 'km/h', min: 0, step: 0.1 },
  { key: 'accel_sign_cooldown', label: '加減速切替抑制', unit: 's', min: 0, step: 0.1 },
  { key: 'idm_delta', label: 'IDM 速度指数', unit: '', min: 1, step: 0.1 }
];

const FRAME_BUFFER_LOW_WATERMARK = 30;
const FRAME_BUFFER_TARGET = 120;
const FRAME_REQUEST_BATCH = 60;
const RECORDING_MAX_SECONDS = 300;
const RECORDING_FPS = 5;
const RECORDING_MIN_FRAME_INTERVAL_MS = 1000 / RECORDING_FPS;
const RECORDING_DEFAULT_DELAY_MS = 100;

const deepClone = (value) => JSON.parse(JSON.stringify(value));

const normalizeNetworkForHistory = (value) => ({
  stations: value?.stations || [],
  segments: value?.segments || [],
  routes: value?.routes || [],
  exclusive_sections: value?.exclusive_sections || [],
  interlocking_devices: value?.interlocking_devices || []
});

export default function App() {
  const [network, setNetwork] = useState(null);
  const [trains, setTrains] = useState([]);
  const [trainStates, setTrainStates] = useState([]);
  const [logEntries, setLogEntries] = useState([]);
  const [running, setRunning] = useState(false);
  const [ws, setWs] = useState(null);
  const [dt, setDt] = useState(0.5);
  const [duration, setDuration] = useState(120);
  const [outputInterval, setOutputInterval] = useState(1.0);
  const [playbackSpeed, setPlaybackSpeed] = useState(10);
  // ▼▼▼ これを1行追加 ▼▼▼
  const [llmInterval, setLlmInterval] = useState(30);
  const [simulationMode, setSimulationMode] = useState('low_precision');
  const [idmTimeHeadway, setIdmTimeHeadway] = useState(1.5);
  const [headwayTarget, setHeadwayTarget] = useState(120);
  const [headwayEpsilon, setHeadwayEpsilon] = useState(10);
  const [vehicleParams, setVehicleParams] = useState(DEFAULT_VEHICLE_PARAMS);
  const [vehicleSettingsOpen, setVehicleSettingsOpen] = useState(false);
  const [recordingEnabled, setRecordingEnabled] = useState(false);
  const [recordingStatus, setRecordingStatus] = useState('idle');
  const [recordingDownloadUrl, setRecordingDownloadUrl] = useState('');
  const [recordingFrameCount, setRecordingFrameCount] = useState(0);
  const [recordingError, setRecordingError] = useState('');
  const [activeTab, setActiveTab] = useState('simulation');
  const [routeDesignName, setRouteDesignName] = useState('');
  const [routeDesignHistory, setRouteDesignHistory] = useState([]);
  const [selectedRouteDesignId, setSelectedRouteDesignId] = useState('');
  const [backendBaseOverride, setBackendBaseOverride] = useState('');
  const [selectedStation, setSelectedStation] = useState(null);
  const [selectedInterlockingDevice, setSelectedInterlockingDevice] = useState(null);
  const [connectionMode, setConnectionMode] = useState(false);
  const [firstStationForConnection, setFirstStationForConnection] = useState(null);
  const [routeDraft, setRouteDraft] = useState({ name: '', station_ids: [] });
  const [routeEditing, setRouteEditing] = useState(false);
  const [sectionDraft, setSectionDraft] = useState({ name: '', route_ids: [], segment_ids: [] });
  const [interlockDraft, setInterlockDraft] = useState({ name: '', route_id: '', section_id: '', approach_distance: 150, stop_margin: 0 });
  const [sidebarWidth, setSidebarWidth] = useState(320);
  const [infoPaneHeight, setInfoPaneHeight] = useState(280);
  const [canvasSize, setCanvasSize] = useState({ width: 900, height: 420 });

  // ▼▼▼ これを1行追加 ▼▼▼
  //const [isLlmThinking, setIsLlmThinking] = useState(false);
  const [llmStatus, setLlmStatus] = useState('idle'); // 'idle' | 'thinking' | 'success' | 'error'

  const bufferRef = useRef([]);
  const streamingRef = useRef(false);
  const playbackTimerRef = useRef(null);
  const playbackSpeedRef = useRef(playbackSpeed);
  const frameRequestInFlightRef = useRef(false);
  const wsRef = useRef(null);
  const resizeModeRef = useRef(null);
  const canvasContainerRef = useRef(null);
  const simulationStageRef = useRef(null);
  const selectedInterlockingRowRef = useRef(null);
  const recordingRef = useRef({
    active: false,
    token: 0,
    gif: null,
    pendingFrame: null,
    startedAtSimulationTime: null,
    lastCaptureRealMs: 0,
    frameCount: 0,
    captureWidth: 0,
    captureHeight: 0,
    captureX: 0,
    captureY: 0,
    captureScaleX: 1,
    captureScaleY: 1,
    objectUrl: ''
  });

  const stations = network?.stations || [];
  const segments = network?.segments || [];
  const routes = network?.routes || [];
  const exclusiveSections = network?.exclusive_sections || [];
  const interlockingDevices = network?.interlocking_devices || [];
  const resizerSize = 6;

  const segmentById = useMemo(
    () => new Map(segments.map((segment) => [segment.id, segment])),
    [segments]
  );
  const stationById = useMemo(
    () => new Map(stations.map((station) => [station.id, station])),
    [stations]
  );
  const routeById = useMemo(
    () => new Map(routes.map((route) => [route.id, route])),
    [routes]
  );

  const getNetworkValidationError = (value) => {
    if (!value || typeof value !== 'object') return 'network がオブジェクトではありません';
    if (!Array.isArray(value.stations) || !Array.isArray(value.segments) || !Array.isArray(value.routes)) {
      return 'stations / segments / routes が配列ではありません';
    }
    if (!Array.isArray(value.exclusive_sections)) return 'exclusive_sections が配列ではありません';
    if (value.interlocking_devices != null && !Array.isArray(value.interlocking_devices)) return 'interlocking_devices が配列ではありません';

    const stationIds = new Set();
    for (const station of value.stations) {
      if (!station?.id) return 'ID が空の地点があります';
      if (stationIds.has(station.id)) return `地点IDが重複しています: ${station.id}`;
      if (!Number.isFinite(station.x) || !Number.isFinite(station.y)) return `地点 ${station.id} の座標が不正です`;
      if (station.kind != null && !['station', 'waypoint'].includes(station.kind)) return `地点 ${station.id} の種類が不正です`;
      if (station.length != null && (!Number.isFinite(station.length) || station.length < 0)) return `地点 ${station.id} の駅長さが不正です`;
      if (station.stop_time != null && (!Number.isFinite(station.stop_time) || station.stop_time < 0)) return `地点 ${station.id} の停車時間が不正です`;
      stationIds.add(station.id);
    }

    const segmentIds = new Set();
    const segmentMap = new Map();
    for (const segment of value.segments) {
      if (!segment?.id) return 'ID が空の線路があります';
      if (segmentIds.has(segment.id)) return `線路IDが重複しています: ${segment.id}`;
      if (!stationIds.has(segment.start) || !stationIds.has(segment.end)) return `線路 ${segment.id} の開始/終了地点が存在しません: ${segment.start} -> ${segment.end}`;
      if (segment.length != null && (!Number.isFinite(segment.length) || segment.length <= 0)) return `線路 ${segment.id} の長さが不正です`;
      segmentIds.add(segment.id);
      segmentMap.set(segment.id, segment);
    }

    const routeIds = new Set();
    for (const route of value.routes) {
      if (!route?.id) return 'ID が空の路線があります';
      if (routeIds.has(route.id)) return `路線IDが重複しています: ${route.id}`;
      routeIds.add(route.id);
      if (!Array.isArray(route.legs) || route.legs.length === 0) return `路線 ${route.id} に経路がありません`;
      let lastTo = null;
      for (const leg of route.legs) {
        const segment = segmentMap.get(leg.segment_id);
        if (!segment) return `路線 ${route.id} が存在しない線路を参照しています: ${leg.segment_id}`;
        const forward = leg.from === segment.start && leg.to === segment.end;
        const reverse = leg.from === segment.end && leg.to === segment.start;
        if (!forward && !reverse) return `路線 ${route.id} の経路が線路 ${leg.segment_id} の端点と一致していません`;
        if (lastTo !== null && lastTo !== leg.from) return `路線 ${route.id} の経路が途中で途切れています: ${lastTo} -> ${leg.from}`;
        lastTo = leg.to;
      }
    }

    for (const section of value.exclusive_sections) {
      if (!section?.id) return 'ID が空の排他区間があります';
      if (!Array.isArray(section.segment_ids) || section.segment_ids.length === 0) return `排他区間 ${section.id} に対象線路がありません`;
      const missingSegments = section.segment_ids.filter((id) => !segmentIds.has(id));
      if (missingSegments.length > 0) return `排他区間 ${section.id} が存在しない線路を参照しています: ${missingSegments.join(', ')}`;
      const priorityRouteIds = section.priority_route_ids || [];
      const missingRoutes = priorityRouteIds.filter((id) => !routeIds.has(id));
      if (missingRoutes.length > 0) return `排他区間 ${section.id} が存在しない優先路線を参照しています: ${missingRoutes.join(', ')}`;
    }

    const sectionIds = new Set(value.exclusive_sections.map((section) => section.id));
    const interlockIds = new Set();
    for (const device of value.interlocking_devices || []) {
      if (!device?.id) return 'ID が空の連動装置があります';
      if (interlockIds.has(device.id)) return `連動装置IDが重複しています: ${device.id}`;
      if (!routeIds.has(device.route_id)) return `連動装置 ${device.id} が存在しない路線を参照しています: ${device.route_id}`;
      if (!sectionIds.has(device.section_id)) return `連動装置 ${device.id} が存在しない排他区間を参照しています: ${device.section_id}`;
      const route = value.routes.find((item) => item.id === device.route_id);
      const section = value.exclusive_sections.find((item) => item.id === device.section_id);
      const routeSegmentIds = new Set((route?.legs || []).map((leg) => leg.segment_id));
      if (section && !(section.segment_ids || []).some((segmentId) => routeSegmentIds.has(segmentId))) return `連動装置 ${device.id} の路線は対象排他区間を通過しません`;
      if (!Number.isFinite(device.approach_distance) || device.approach_distance < 0) return `連動装置 ${device.id} の判定距離が不正です`;
      if (device.stop_margin != null && (!Number.isFinite(device.stop_margin) || device.stop_margin < 0)) return `連動装置 ${device.id} の停止余裕が不正です`;
      interlockIds.add(device.id);
    }

    return '';
  };

  const isValidNetwork = (value) => getNetworkValidationError(value) === '';

  useEffect(() => {
    if (!selectedInterlockingDevice) return;
    if (interlockingDevices.some((device) => device.id === selectedInterlockingDevice)) return;
    setSelectedInterlockingDevice(null);
  }, [interlockingDevices, selectedInterlockingDevice]);

  useEffect(() => {
    if (activeTab !== 'routeEditor' || !selectedInterlockingDevice) return;
    selectedInterlockingRowRef.current?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }, [activeTab, selectedInterlockingDevice]);

  useEffect(() => {
    if (selectedStation) {
      setSelectedInterlockingDevice(null);
    }
  }, [selectedStation]);

  const getBackendBase = () => {
    if (backendBaseOverride) return backendBaseOverride;
    const envBase = import.meta.env.VITE_BACKEND_URL;
    if (envBase) return envBase.replace(/\/$/, '');
    const protocol = location.protocol === 'https:' ? 'https' : 'http';
    return `${protocol}://${location.hostname}:8000`;
  };

  const getWebSocketUrl = () => {
    const base = getBackendBase();
    if (base.startsWith('ws://') || base.startsWith('wss://')) {
      return `${base.replace(/\/$/, '')}/ws/sim`;
    }
    const wsProto = base.startsWith('https://') ? 'wss://' : 'ws://';
    const host = base.replace(/^https?:\/\//, '');
    return `${wsProto}${host}/ws/sim`;
  };

  const extractErrorMessage = async (error, fallback) => {
    const response = error?.response;
    if (response?.data instanceof Blob) {
      try {
        const text = await response.data.text();
        const json = JSON.parse(text);
        return json?.error || fallback;
      } catch {
        return fallback;
      }
    }
    return response?.data?.error || fallback;
  };

  const fetchNetworkFromApi = async () => {
    const candidates = [getBackendBase(), 'http://localhost:8000', 'http://127.0.0.1:8000'];
    let lastError = null;
    for (const base of [...new Set(candidates.map((c) => c.replace(/\/$/, '')))]) {
      try {
        const response = await axios.get(`${base}/api/network`);
        if (isValidNetwork(response.data)) {
          setNetwork({ ...response.data, interlocking_devices: response.data.interlocking_devices || [] });
          setBackendBaseOverride(base);
          return;
        }
        lastError = new Error('networkの形式が不正です');
      } catch (error) {
        lastError = error;
      }
    }
    alert(await extractErrorMessage(lastError, 'networkの取得に失敗しました'));
  };

  const fetchRouteDesignsFromApi = async () => {
    const candidates = [getBackendBase(), 'http://localhost:8000', 'http://127.0.0.1:8000'];
    for (const base of [...new Set(candidates.map((c) => c.replace(/\/$/, '')))]) {
      try {
        const response = await axios.get(`${base}/api/route-designs`);
        const designs = Array.isArray(response.data?.designs) ? response.data.designs : [];
        setRouteDesignHistory(designs);
        setSelectedRouteDesignId((current) => (
          designs.some((entry) => entry.id === current) ? current : designs[0]?.id || ''
        ));
        setBackendBaseOverride(base);
        return;
      } catch {
        // try next backend candidate
      }
    }
  };

  useEffect(() => {
    fetchRouteDesignsFromApi();
    const savedNetwork = localStorage.getItem(NETWORK_STORAGE_KEY);
    if (savedNetwork) {
      try {
        const parsed = JSON.parse(savedNetwork);
        if (isValidNetwork(parsed)) {
          setNetwork({ ...parsed, interlocking_devices: parsed.interlocking_devices || [] });
          return;
        }
      } catch {
        // fall through
      }
      localStorage.removeItem(NETWORK_STORAGE_KEY);
    }
    localStorage.removeItem('route_network');
    fetchNetworkFromApi();
  }, []);

  useEffect(() => {
    if (isValidNetwork(network)) {
      localStorage.setItem(NETWORK_STORAGE_KEY, JSON.stringify(network));
    }
  }, [network]);

  useEffect(() => {
    const updateCanvasSize = () => {
      const rect = canvasContainerRef.current?.getBoundingClientRect();
      if (!rect) return;
      setCanvasSize({
        width: Math.max(320, rect.width),
        height: Math.max(220, rect.height)
      });
    };
    updateCanvasSize();
    window.addEventListener('resize', updateCanvasSize);
    return () => window.removeEventListener('resize', updateCanvasSize);
  }, [activeTab, sidebarWidth, infoPaneHeight]);

  useEffect(() => {
    const handleMouseMove = (e) => {
      if (resizeModeRef.current === 'sidebar') {
        setSidebarWidth(Math.max(240, e.clientX));
      }
      if (resizeModeRef.current === 'info') {
        const fromBottom = document.body.getBoundingClientRect().bottom - e.clientY;
        setInfoPaneHeight(Math.max(180, fromBottom));
      }
    };
    const handleMouseUp = () => {
      resizeModeRef.current = null;
      document.body.style.cursor = 'default';
      document.body.style.userSelect = 'auto';
    };
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, []);

  useEffect(() => {
    playbackSpeedRef.current = playbackSpeed;
    const currentWs = wsRef.current;
    if (currentWs?.readyState === WebSocket.OPEN) {
      currentWs.send(JSON.stringify({ type: 'set_playback_speed', value: playbackSpeed }));
    }
  }, [playbackSpeed]);

  const getBufferedFrameCount = () => {
    const times = new Set();
    for (const state of bufferRef.current) {
      if (state && Number.isFinite(state.time)) times.add(state.time);
    }
    return times.size;
  };

  const requestMoreFrames = (socket = ws, force = false) => {
    if (!socket || socket.readyState !== WebSocket.OPEN) return;
    if (!streamingRef.current || frameRequestInFlightRef.current) return;
    const bufferedFrames = getBufferedFrameCount();
    if (!force && bufferedFrames >= FRAME_BUFFER_LOW_WATERMARK) return;
    if (bufferedFrames >= FRAME_BUFFER_TARGET) return;
    const count = Math.min(FRAME_REQUEST_BATCH, Math.max(1, FRAME_BUFFER_TARGET - bufferedFrames));
    frameRequestInFlightRef.current = true;
    socket.send(JSON.stringify({ type: 'request_frames', count }));
  };

  const attachWebSocketHandlers = (socket) => {
    socket.onmessage = (evt) => {
      if (wsRef.current !== socket) return;
      try {
        const data = JSON.parse(evt.data);
        if (data?.error) {
          alert(data.error);
          socket.close();
          return;
        }
        if (data?.type === 'batch_complete') {
          frameRequestInFlightRef.current = false;
          if (data.complete) {
            streamingRef.current = false;
            if (bufferRef.current.length === 0) setRunning(false);
          } else {
            requestMoreFrames(socket);
          }
          return;
        }
        bufferRef.current.push(data);
      } catch (error) {
        console.error('[WebSocket parse error]', error);
      }
    };
    socket.onclose = () => {
      if (wsRef.current !== socket) return;
      streamingRef.current = false;
      frameRequestInFlightRef.current = false;
      wsRef.current = null;
      setWs(null);
      if (bufferRef.current.length === 0) setRunning(false);
    };
    socket.onerror = (error) => {
      if (wsRef.current !== socket) return;
      console.error('[WebSocket error]', error);
    };
  };

  const clearPlaybackTimer = () => {
    if (playbackTimerRef.current) {
      clearInterval(playbackTimerRef.current);
      playbackTimerRef.current = null;
    }
  };

  const simulationTimeFromStates = (states) => (
    states.length
      ? Math.max(...states.map((state) => Number.isFinite(state.time) ? state.time : 0))
      : null
  );

  const discardRecording = () => {
    const previous = recordingRef.current;
    if (previous.objectUrl) {
      window.URL.revokeObjectURL(previous.objectUrl);
    }
    recordingRef.current = {
      active: false,
      token: previous.token + 1,
      gif: null,
      pendingFrame: null,
      startedAtSimulationTime: null,
      lastCaptureRealMs: 0,
      frameCount: 0,
      captureWidth: 0,
      captureHeight: 0,
      captureX: 0,
      captureY: 0,
      captureScaleX: 1,
      captureScaleY: 1,
      objectUrl: ''
    };
    setRecordingDownloadUrl('');
    setRecordingFrameCount(0);
    setRecordingError('');
    setRecordingStatus('idle');
  };

  const beginRecording = () => {
    const previous = recordingRef.current;
    if (previous.objectUrl) {
      window.URL.revokeObjectURL(previous.objectUrl);
    }
    const stage = simulationStageRef.current;
    const rect = canvasContainerRef.current?.getBoundingClientRect();
    const captureWidth = Math.max(1, Math.round(stage?.width?.() || rect?.width || canvasSize.width));
    const captureHeight = Math.max(1, Math.round(stage?.height?.() || rect?.height || canvasSize.height));
    const captureX = Number.isFinite(stage?.x?.()) ? stage.x() : 0;
    const captureY = Number.isFinite(stage?.y?.()) ? stage.y() : 0;
    const captureScaleX = Number.isFinite(stage?.scaleX?.()) ? stage.scaleX() : 1;
    const captureScaleY = Number.isFinite(stage?.scaleY?.()) ? stage.scaleY() : 1;
    recordingRef.current = {
      active: true,
      token: previous.token + 1,
      gif: GIFEncoder(),
      pendingFrame: null,
      startedAtSimulationTime: null,
      lastCaptureRealMs: 0,
      frameCount: 0,
      captureWidth,
      captureHeight,
      captureX,
      captureY,
      captureScaleX,
      captureScaleY,
      objectUrl: ''
    };
    setRecordingDownloadUrl('');
    setRecordingFrameCount(0);
    setRecordingError('');
    setRecordingStatus('recording');
  };

  const writeRecordingFrame = (frame, delayMs) => {
    const gif = recordingRef.current.gif;
    if (!gif || !frame) return;
    const format = 'rgb444';
    const palette = quantize(frame.data, 256, { format });
    const index = applyPalette(frame.data, palette, format);
    const delay = Math.round(Math.max(20, Math.min(1000, delayMs || RECORDING_DEFAULT_DELAY_MS)));
    gif.writeFrame(index, frame.width, frame.height, { palette, delay, repeat: 0 });
  };

  const buildRecordingFrame = () => {
    const stage = simulationStageRef.current;
    if (!stage) return null;
    const rec = recordingRef.current;
    const width = Math.max(1, Math.round(rec.captureWidth || stage.width()));
    const height = Math.max(1, Math.round(rec.captureHeight || stage.height()));
    const currentView = {
      width: stage.width(),
      height: stage.height(),
      x: stage.x(),
      y: stage.y(),
      scaleX: stage.scaleX(),
      scaleY: stage.scaleY()
    };
    let sourceCanvas = null;
    try {
      stage.width(width);
      stage.height(height);
      stage.x(rec.captureX);
      stage.y(rec.captureY);
      stage.scaleX(rec.captureScaleX);
      stage.scaleY(rec.captureScaleY);
      sourceCanvas = stage.toCanvas({ x: 0, y: 0, width, height, pixelRatio: 1 });
    } finally {
      stage.width(currentView.width);
      stage.height(currentView.height);
      stage.x(currentView.x);
      stage.y(currentView.y);
      stage.scaleX(currentView.scaleX);
      stage.scaleY(currentView.scaleY);
      stage.batchDraw?.();
    }
    if (!sourceCanvas?.width || !sourceCanvas?.height) return null;
    const frameCanvas = document.createElement('canvas');
    frameCanvas.width = width;
    frameCanvas.height = height;
    const context = frameCanvas.getContext('2d', { willReadFrequently: true });
    if (!context) return null;
    context.fillStyle = '#ffffff';
    context.fillRect(0, 0, width, height);
    context.drawImage(sourceCanvas, 0, 0);
    return {
      data: context.getImageData(0, 0, width, height).data,
      width,
      height,
      capturedAt: performance.now()
    };
  };

  const finalizeRecording = () => {
    const rec = recordingRef.current;
    if (!rec.active || !rec.gif) return;
    rec.active = false;
    const token = rec.token;
    setRecordingStatus('encoding');
    window.setTimeout(() => {
      const current = recordingRef.current;
      if (current.token !== token || !current.gif) return;
      try {
        if (current.pendingFrame) {
          writeRecordingFrame(current.pendingFrame, RECORDING_DEFAULT_DELAY_MS);
          current.pendingFrame = null;
        }
        if (current.frameCount === 0) {
          current.gif = null;
          setRecordingStatus('idle');
          return;
        }
        current.gif.finish();
        const blob = new Blob([current.gif.bytes()], { type: 'image/gif' });
        if (current.objectUrl) {
          window.URL.revokeObjectURL(current.objectUrl);
        }
        const url = window.URL.createObjectURL(blob);
        current.objectUrl = url;
        current.gif = null;
        setRecordingDownloadUrl(url);
        setRecordingStatus('ready');
      } catch (error) {
        console.error('[GIF recording error]', error);
        current.gif = null;
        current.pendingFrame = null;
        setRecordingError('録画GIFの生成に失敗しました');
        setRecordingStatus('error');
      }
    }, 0);
  };

  const captureRecordingFrame = (simulationTime) => {
    const rec = recordingRef.current;
    if (!rec.active || !rec.gif || !Number.isFinite(simulationTime)) return;
    if (rec.startedAtSimulationTime == null) {
      rec.startedAtSimulationTime = simulationTime;
    }
    const elapsed = simulationTime - rec.startedAtSimulationTime;
    if (elapsed > RECORDING_MAX_SECONDS + 1e-9) {
      finalizeRecording();
      return;
    }
    const now = performance.now();
    if (rec.lastCaptureRealMs && now - rec.lastCaptureRealMs < RECORDING_MIN_FRAME_INTERVAL_MS) {
      if (elapsed >= RECORDING_MAX_SECONDS - 1e-9) finalizeRecording();
      return;
    }
    const frame = buildRecordingFrame();
    if (!frame) return;
    if (rec.pendingFrame) {
      writeRecordingFrame(rec.pendingFrame, frame.capturedAt - rec.pendingFrame.capturedAt);
    }
    rec.pendingFrame = frame;
    rec.lastCaptureRealMs = frame.capturedAt;
    rec.frameCount += 1;
    setRecordingFrameCount(rec.frameCount);
    if (elapsed >= RECORDING_MAX_SECONDS - 1e-9) {
      finalizeRecording();
    }
  };

  const downloadRecordingGif = () => {
    if (!recordingDownloadUrl) return;
    const a = document.createElement('a');
    a.href = recordingDownloadUrl;
    a.download = 'simulation-recording.gif';
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  const drainNextFrame = () => {
    const buffer = bufferRef.current;
    if (buffer.length === 0) return false;

    // ▼▼▼ 修正: ステータスの受け取りとタイマー処理 ▼▼▼
    while (buffer.length > 0 && buffer[0].type === 'llm_status') {
      const event = buffer.shift();
      setLlmStatus(event.status);
      
      // 結果表示後、2秒後に自動で消す
      if (event.status === 'success' || event.status === 'error') {
        setTimeout(() => {
          setLlmStatus((prev) => (prev === event.status ? 'idle' : prev));
        }, 2000);
      }
    }
    
    if (buffer.length === 0) return true; 
    // ▲▲▲ 修正ここまで ▲▲▲

    const frameTime = buffer[0].time;
    const frameStates = [];
    while (buffer.length > 0 && buffer[0].time === frameTime) {
      frameStates.push(buffer.shift());
    }
    setTrainStates((prev) => {
      const next = new Map(prev.map((state) => [state.train_id, state]));
      frameStates.forEach((state) => {
        if (state.status === 'FINISHED') {
          next.delete(state.train_id);
        } else {
          next.set(state.train_id, state);
        }
      });
      return Array.from(next.values());
    });
    setLogEntries((prev) => {
      const entries = frameStates.map((state) => ({
        time: state.time,
        train_id: state.train_id,
        route_id: state.route_id,
        speed: state.speed,
        status: state.status,
        wait_reason: state.wait_reason,
        block_id: state.block_id,
        control_reason: state.control_reason,
        control_block_id: state.control_block_id,
        in_shared_section: state.in_shared_section,
        segment_id: state.segment_id,
        segment_ids: state.segment_ids,
        stop_remaining: state.stop_remaining,
        route_distance: state.route_distance
      }));
      const combined = prev.concat(entries);
      return combined.length <= 3000 ? combined : combined.slice(combined.length - 3000);
    });
    requestMoreFrames();
    return true;
  };

  useEffect(() => {
    clearPlaybackTimer();
    if (!running) return;
    const intervalSeconds = Number.isFinite(outputInterval) && outputInterval > 0 ? outputInterval : dt;
    const intervalMs = Math.max(16, (intervalSeconds * 1000) / Math.max(0.1, playbackSpeedRef.current));
    playbackTimerRef.current = setInterval(() => {
      const advanced = drainNextFrame();
      if (!advanced && !streamingRef.current) {
        clearPlaybackTimer();
        setRunning(false);
      }
    }, intervalMs);
    return () => clearPlaybackTimer();
  }, [running, dt, outputInterval, ws]);

  useEffect(() => {
    captureRecordingFrame(simulationTimeFromStates(trainStates));
  }, [trainStates]);

  useEffect(() => {
    if (!running) finalizeRecording();
  }, [running]);

  useEffect(() => () => {
    const url = recordingRef.current.objectUrl;
    if (url) window.URL.revokeObjectURL(url);
  }, []);

  const normalizeStartTime = (value) => (Number.isFinite(value) && value >= 0 ? value : 0);

  const sanitizeVehicleParams = (params) => {
    const next = {};
    for (const field of VEHICLE_PARAM_FIELDS) {
      const fallback = DEFAULT_VEHICLE_PARAMS[field.key];
      const value = Number(params?.[field.key]);
      next[field.key] = Number.isFinite(value) ? Math.max(field.min, value) : fallback;
    }
    return next;
  };

  const updateVehicleParam = (key, rawValue) => {
    const value = parseFloat(rawValue);
    setVehicleParams((prev) => ({
      ...prev,
      [key]: Number.isFinite(value) ? value : DEFAULT_VEHICLE_PARAMS[key]
    }));
  };

  const applyVehicleParamsToTrains = () => {
    const params = sanitizeVehicleParams(vehicleParams);
    setTrains((prev) => prev.map((train) => ({
      ...train,
      max_speed: params.max_speed,
      length: params.length,
      accel: params.accel,
      decel: params.decel
    })));
  };

  const buildPayload = () => ({
    network,
    trains: trains.map((train) => ({
      ...train,
      route_id: train.route_id,
      start_time: normalizeStartTime(train.start_time)
    })),
    dt,
    duration,
    output_interval: outputInterval,
    simulation_mode: simulationMode,
    // ▼▼▼ これを1行追加 ▼▼▼
    llm_interval: llmInterval,
    idm_T: idmTimeHeadway,
    headway_target: headwayTarget,
    headway_epsilon: headwayEpsilon,
    vehicle_params: sanitizeVehicleParams(vehicleParams)
  });

  const validateSimulationInputs = () => {
    const networkError = getNetworkValidationError(network);
    if (networkError) {
      alert(`ネットワークが不正です。${networkError}`);
      return false;
    }
    if (!Array.isArray(trains) || trains.length === 0) {
      alert('列車が設定されていません');
      return false;
    }
    for (const train of trains) {
      if (!train.train_id) {
        alert('列車IDが空です');
        return false;
      }
      if (!routeById.has(train.route_id)) {
        alert(`列車 ${train.train_id} の route_id が存在しません: ${train.route_id}`);
        return false;
      }
      if (!Number.isFinite(train.length) || train.length <= 0) {
        alert(`列車 ${train.train_id} の長さは0より大きい数値で入力してください`);
        return false;
      }
      if (!Number.isFinite(train.start_time) || train.start_time < 0) {
        alert(`列車 ${train.train_id} の出発時刻は0以上の数値で入力してください`);
        return false;
      }
    }
    return true;
  };

  const start = async () => {
    if (!validateSimulationInputs()) return;
    if (!await saveCurrentRouteDesignForSimulation()) return;
    if (recordingEnabled) {
      beginRecording();
    } else {
      discardRecording();
    }
    clearPlaybackTimer();
    const previousWs = wsRef.current;
    if (previousWs && previousWs.readyState !== WebSocket.CLOSED) {
      try {
        if (previousWs.readyState === WebSocket.OPEN) {
          previousWs.send(JSON.stringify({ command: 'stop' }));
        }
      } catch {
        // ignore
      }
      previousWs.close();
    }
    wsRef.current = null;
    setTrainStates([]);
    setLogEntries([]);
    bufferRef.current = [];
    streamingRef.current = true;
    frameRequestInFlightRef.current = false;
    const socket = new WebSocket(getWebSocketUrl());
    attachWebSocketHandlers(socket);
    socket.onopen = () => {
      if (wsRef.current !== socket) return;
      socket.send(JSON.stringify(buildPayload()));
      socket.send(JSON.stringify({ type: 'set_playback_speed', value: playbackSpeedRef.current }));
      requestMoreFrames(socket, true);
    };
    wsRef.current = socket;
    setWs(socket);
    setRunning(true);
  };

  const stop = () => {
    const currentWs = wsRef.current;
    if (currentWs && currentWs.readyState !== WebSocket.CLOSED) {
      try {
        if (currentWs.readyState === WebSocket.OPEN) {
          currentWs.send(JSON.stringify({ command: 'stop' }));
        }
      } catch {
        // ignore
      }
      currentWs.close();
    }
    wsRef.current = null;
    setWs(null);
    streamingRef.current = false;
    frameRequestInFlightRef.current = false;
    bufferRef.current = [];
    setRunning(false);
  };

  const downloadCSV = async () => {
    if (!validateSimulationInputs()) return;
    if (!await saveCurrentRouteDesignForSimulation()) return;
    try {
      const response = await axios.post(`${getBackendBase()}/api/simulate`, buildPayload(), { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([response.data], { type: 'text/csv' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = 'simulation.csv';
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.setTimeout(() => window.URL.revokeObjectURL(url), 0);
    } catch (error) {
      alert(await extractErrorMessage(error, 'CSVダウンロードに失敗しました'));
    }
  };

  const getSegmentDistance = (segment) => {
    if (Number.isFinite(segment?.length) && segment.length > 0) return segment.length;
    const start = stationById.get(segment?.start);
    const end = stationById.get(segment?.end);
    if (!start || !end) return 0;
    return Math.hypot(end.x - start.x, end.y - start.y);
  };

  const computeTravelTimeSeconds = (lengthMeters, maxSpeedKmh, accelKmhS, decelKmhS) => {
    if (!Number.isFinite(lengthMeters) || lengthMeters <= 0) return null;
    if (!Number.isFinite(maxSpeedKmh) || maxSpeedKmh <= 0) return null;
    if (!Number.isFinite(accelKmhS) || accelKmhS <= 0) return null;
    if (!Number.isFinite(decelKmhS) || decelKmhS <= 0) return null;
    const vmax = maxSpeedKmh / 3.6;
    const a = accelKmhS / 3.6;
    const b = decelKmhS / 3.6;
    const dAcc = (vmax * vmax) / (2 * a);
    const dDec = (vmax * vmax) / (2 * b);
    if (dAcc + dDec <= lengthMeters) {
      return (vmax / a) + (vmax / b) + ((lengthMeters - dAcc - dDec) / vmax);
    }
    const vPeak = Math.sqrt((2 * a * b * lengthMeters) / (a + b));
    return (vPeak / a) + (vPeak / b);
  };

  const getTravelTimeParams = () => {
    const ref = trains[0] || {};
    const params = sanitizeVehicleParams(vehicleParams);
    return {
      max_speed: Number.isFinite(ref.max_speed) ? ref.max_speed : params.max_speed,
      accel: Number.isFinite(ref.accel) ? ref.accel : params.accel,
      decel: Number.isFinite(ref.decel) ? ref.decel : params.decel
    };
  };

  const setNetworkSafely = (next) => {
    setNetwork({
      stations: next.stations || [],
      segments: next.segments || [],
      routes: next.routes || [],
      exclusive_sections: next.exclusive_sections || [],
      interlocking_devices: next.interlocking_devices || []
    });
  };

  const saveCurrentRouteDesignForSimulation = async () => {
    if (!isValidNetwork(network)) return false;
    try {
      const response = await axios.post(`${getBackendBase()}/api/route-designs`, {
        name: routeDesignName.trim(),
        network: normalizeNetworkForHistory(network)
      });
      const designs = Array.isArray(response.data?.designs) ? response.data.designs : [];
      const design = response.data?.design;
      setRouteDesignHistory(designs);
      if (design?.id) {
        setSelectedRouteDesignId(design.id);
        setRouteDesignName(design.name || '');
      }
      return true;
    } catch (error) {
      alert(await extractErrorMessage(error, '経路設計の保存に失敗しました'));
      return false;
    }
  };

  const loadRouteDesign = () => {
    const entry = routeDesignHistory.find((item) => item.id === selectedRouteDesignId);
    if (!entry) {
      alert('呼び出す経路設計を選択してください');
      return;
    }
    const nextNetwork = deepClone(normalizeNetworkForHistory(entry.network));
    if (!isValidNetwork(nextNetwork)) {
      alert('保存されている経路設計の形式が不正です');
      return;
    }
    setNetworkSafely(nextNetwork);
    setRouteDesignName(entry.name || '');
    setRouteEditing(false);
    setRouteDraft({ name: '', station_ids: [] });
    setSectionDraft({ name: '', route_ids: [], segment_ids: [] });
    setInterlockDraft({ name: '', route_id: nextNetwork.routes[0]?.id || '', section_id: '', approach_distance: 150, stop_margin: 0 });
    setSelectedStation(null);
    setSelectedInterlockingDevice(null);
    setConnectionMode(false);
    setFirstStationForConnection(null);
    const validRouteIds = new Set(nextNetwork.routes.map((route) => route.id));
    const fallbackRouteId = nextNetwork.routes[0]?.id || '';
    setTrains((prev) => prev.map((train) => (
      validRouteIds.has(train.route_id) ? train : { ...train, route_id: fallbackRouteId }
    )));
  };

  const updateStation = (stationId, patch) => {
    setNetworkSafely({
      ...network,
      stations: stations.map((station) => (station.id === stationId ? { ...station, ...patch } : station))
    });
  };

  const routeUsesAnySegment = (route, segmentIds) => (
    (route.legs || []).some((leg) => segmentIds.has(leg.segment_id))
  );

  const removeDanglingRouteRefs = (sections, validRoutes) => {
    const validRouteIds = new Set(validRoutes.map((route) => route.id));
    return (sections || []).map((section) => ({
      ...section,
      priority_route_ids: (section.priority_route_ids || []).filter((id) => validRouteIds.has(id))
    }));
  };

  const removeDanglingInterlockingRefs = (devices, validRoutes, validSections) => {
    const validRouteIds = new Set(validRoutes.map((route) => route.id));
    const validSectionIds = new Set(validSections.map((section) => section.id));
    return (devices || []).filter((device) => (
      validRouteIds.has(device.route_id) && validSectionIds.has(device.section_id)
    ));
  };

  const deleteStationFromRoute = (stationId) => {
    const removedSegments = new Set(segments.filter((seg) => seg.start === stationId || seg.end === stationId).map((seg) => seg.id));
    const nextRoutes = routes.filter((route) => !routeUsesAnySegment(route, removedSegments));
    const nextSections = removeDanglingRouteRefs(exclusiveSections
      .map((section) => ({ ...section, segment_ids: section.segment_ids.filter((id) => !removedSegments.has(id)) }))
      .filter((section) => section.segment_ids.length > 0), nextRoutes);
    setNetworkSafely({
      ...network,
      stations: stations.filter((station) => station.id !== stationId),
      segments: segments.filter((seg) => !removedSegments.has(seg.id)),
      routes: nextRoutes,
      exclusive_sections: nextSections,
      interlocking_devices: removeDanglingInterlockingRefs(interlockingDevices, nextRoutes, nextSections)
    });
    setSelectedStation(null);
  };

  const updateSegmentLength = (segmentId, rawValue) => {
    const params = getTravelTimeParams();
    setNetworkSafely({
      ...network,
      segments: segments.map((segment) => {
        if (segment.id !== segmentId) return segment;
        const length = Number.isFinite(rawValue) && rawValue > 0 ? rawValue : undefined;
        const travel = length ? computeTravelTimeSeconds(length, params.max_speed, params.accel, params.decel) : null;
        const next = { ...segment };
        if (length) {
          next.length = length;
          next.travel_time = Number.isFinite(travel) ? Math.round(travel * 1000) / 1000 : undefined;
        } else {
          delete next.length;
          delete next.travel_time;
        }
        return next;
      })
    });
  };

  //関数追加
  const updateSegmentProperty = (segmentId, key, rawValue) => {
    setNetworkSafely({
      ...network,
      segments: segments.map((segment) => {
        if (segment.id !== segmentId) return segment;
        const value = Number.isFinite(rawValue) ? rawValue : undefined;
        const next = { ...segment };
        if (value !== undefined) {
          next[key] = value;
        } else {
          delete next[key];
        }
        return next;
      })
    });
  };

  const deleteSegmentFromRoute = (segmentId) => {
    const removedSegments = new Set([segmentId]);
    const nextRoutes = routes.filter((route) => !routeUsesAnySegment(route, removedSegments));
    const nextSections = removeDanglingRouteRefs(exclusiveSections
      .map((section) => ({ ...section, segment_ids: section.segment_ids.filter((id) => id !== segmentId) }))
      .filter((section) => section.segment_ids.length > 0), nextRoutes);
    setNetworkSafely({
      ...network,
      segments: segments.filter((segment) => segment.id !== segmentId),
      routes: nextRoutes,
      exclusive_sections: nextSections,
      interlocking_devices: removeDanglingInterlockingRefs(interlockingDevices, nextRoutes, nextSections)
    });
  };

  const stationLabel = (stationId) => {
    const station = stationById.get(stationId);
    if (!station) return stationId || '-';
    const kindLabel = station.kind === 'waypoint' ? '通過点' : '駅';
    return station.name ? `${station.name} (${station.id}/${kindLabel})` : `${station.id}/${kindLabel}`;
  };

  const routeStationIds = (route) => {
    const ids = [];
    for (const [index, leg] of (route?.legs || []).entries()) {
      if (index === 0) ids.push(leg.from);
      ids.push(leg.to);
    }
    return ids;
  };

  const findSegmentBetween = (from, to) => segments.find((segment) => (
    (segment.start === from && segment.end === to) ||
    (segment.start === to && segment.end === from)
  ));

  const buildRouteLegsFromStationIds = (stationIds) => {
    if (stationIds.length < 2) throw new Error('経路は2地点以上で設定してください');
    const missingStations = stationIds.filter((id) => !stationById.has(id));
    if (missingStations.length > 0) {
      throw new Error(`存在しない地点があります: ${missingStations.join(', ')}`);
    }

    const legs = [];
    for (let i = 0; i < stationIds.length - 1; i += 1) {
      const from = stationIds[i];
      const to = stationIds[i + 1];
      if (from === to) throw new Error(`同じ駅が連続しています: ${stationLabel(from)}`);
      const segment = findSegmentBetween(from, to);
      if (!segment) {
        throw new Error(`線路がありません: ${stationLabel(from)} -> ${stationLabel(to)}`);
      }
      const reverse = segment.start === to && segment.end === from;
      if (reverse && segment.bidirectional === false) {
        throw new Error(`片方向線路を逆向きには使えません: ${segment.id}`);
      }
      legs.push({ segment_id: segment.id, from, to });
    }
    return legs;
  };

  const startRouteEditing = () => {
    setRouteDraft({ name: '', station_ids: [] });
    setRouteEditing(true);
    setConnectionMode(false);
    setFirstStationForConnection(null);
    setSelectedStation(null);
  };

  const cancelRouteEditing = () => {
    setRouteDraft({ name: '', station_ids: [] });
    setRouteEditing(false);
  };

  const undoRouteStation = () => {
    setRouteDraft((current) => ({
      ...current,
      station_ids: (current.station_ids || []).slice(0, -1)
    }));
  };

  const handleRouteStationClick = (stationId) => {
    const stationIds = routeDraft.station_ids || [];
    const alreadyClosed = stationIds.length > 1 && stationIds[0] === stationIds[stationIds.length - 1];
    if (alreadyClosed) {
      alert('円環路線は既に閉じています。保存するか、1つ戻してください。');
      return;
    }
    if (stationIds[stationIds.length - 1] === stationId) {
      return;
    }
    if (stationIds.includes(stationId) && stationId !== stationIds[0]) {
      alert('同じ地点は重複して追加できません。円環路線にする場合は起点を最後にもう一度クリックしてください。');
      return;
    }
    if (stationId === stationIds[0] && stationIds.length < 2) {
      alert('円環路線にするには、先に別の地点を1つ以上追加してください。');
      return;
    }
    setRouteDraft({
      ...routeDraft,
      station_ids: [...stationIds, stationId]
    });
  };

  const addRoute = () => {
    try {
      const stationIds = routeDraft.station_ids || [];
      const legs = buildRouteLegsFromStationIds(stationIds);
      const nextId = `R_${Math.max(0, ...routes.map((r) => parseInt(String(r.id).replace(/\D/g, ''), 10) || 0)) + 1}`;
      setNetworkSafely({
        ...network,
        routes: [...routes, { id: nextId, name: routeDraft.name || nextId, legs }]
      });
      setRouteDraft({ name: '', station_ids: [] });
      setRouteEditing(false);
    } catch (error) {
      alert(error.message);
    }
  };

  const deleteRoute = (routeId) => {
    const remainingRoutes = routes.filter((route) => route.id !== routeId);
    const fallbackRouteId = remainingRoutes[0]?.id || '';
    const nextSections = removeDanglingRouteRefs(exclusiveSections, remainingRoutes);
    setNetworkSafely({
      ...network,
      routes: remainingRoutes,
      exclusive_sections: nextSections,
      interlocking_devices: removeDanglingInterlockingRefs(interlockingDevices, remainingRoutes, nextSections)
    });
    setTrains(trains.map((train) => (train.route_id === routeId ? { ...train, route_id: fallbackRouteId } : train)));
    setSectionDraft((current) => ({
      ...current,
      route_ids: (current.route_ids || []).filter((id) => id !== routeId)
    }));
  };

  const routeLabel = (routeId) => {
    const route = routeById.get(routeId);
    if (!route) return routeId || '-';
    return route.name ? `${route.name} (${route.id})` : route.id;
  };

  const segmentLabel = (segmentId) => {
    const segment = segmentById.get(segmentId);
    if (!segment) return segmentId || '-';
    return `${segmentId}: ${stationLabel(segment.start)} - ${stationLabel(segment.end)}`;
  };

  const getRouteSegmentIds = (routeId) => {
    const route = routeById.get(routeId);
    const ordered = [];
    for (const leg of route?.legs || []) {
      if (!ordered.includes(leg.segment_id)) ordered.push(leg.segment_id);
    }
    return ordered;
  };

  const getSectionCandidateSegmentIds = (routeIds) => {
    const selected = (routeIds || []).filter((id) => routeById.has(id));
    if (selected.length === 0) return [];
    const counts = new Map();
    const ordered = [];
    for (const routeId of selected) {
      for (const segmentId of getRouteSegmentIds(routeId)) {
        if (!ordered.includes(segmentId)) ordered.push(segmentId);
        counts.set(segmentId, (counts.get(segmentId) || 0) + 1);
      }
    }
    if (selected.length === 1) return ordered;
    return ordered.filter((segmentId) => counts.get(segmentId) >= 2);
  };

  const setSectionRoutes = (routeId, checked) => {
    const currentRouteIds = sectionDraft.route_ids || [];
    const nextRouteIds = checked
      ? [...currentRouteIds, routeId]
      : currentRouteIds.filter((id) => id !== routeId);
    const candidateSegmentIds = getSectionCandidateSegmentIds(nextRouteIds);
    const currentSegmentIds = sectionDraft.segment_ids || [];
    const keptSegmentIds = currentSegmentIds.filter((id) => candidateSegmentIds.includes(id));
    setSectionDraft({
      ...sectionDraft,
      route_ids: nextRouteIds,
      segment_ids: keptSegmentIds.length > 0 ? keptSegmentIds : candidateSegmentIds
    });
  };

  const setSectionSegment = (segmentId, checked) => {
    const currentSegmentIds = sectionDraft.segment_ids || [];
    setSectionDraft({
      ...sectionDraft,
      segment_ids: checked
        ? [...currentSegmentIds, segmentId]
        : currentSegmentIds.filter((id) => id !== segmentId)
    });
  };

  const addExclusiveSection = () => {
    const routeIds = sectionDraft.route_ids || [];
    if (routeIds.length === 0) {
      alert('対象路線を選択してください');
      return;
    }
    const missingRoutes = routeIds.filter((id) => !routeById.has(id));
    if (missingRoutes.length > 0) {
      alert(`存在しない路線があります: ${missingRoutes.join(', ')}`);
      return;
    }
    const segmentIds = sectionDraft.segment_ids || [];
    if (segmentIds.length === 0) {
      alert('対象線路を選択してください');
      return;
    }
    const missingSegments = segmentIds.filter((id) => !segmentById.has(id));
    if (missingSegments.length > 0) {
      alert(`存在しない線路IDがあります: ${missingSegments.join(', ')}`);
      return;
    }
    const nextId = `X_${Math.max(0, ...exclusiveSections.map((s) => parseInt(String(s.id).replace(/\D/g, ''), 10) || 0)) + 1}`;
    setNetworkSafely({
      ...network,
      exclusive_sections: [
        ...exclusiveSections,
        {
          id: nextId,
          name: sectionDraft.name || nextId,
          segment_ids: segmentIds,
          capacity: 1,
          priority_route_ids: routeIds
        }
      ]
    });
    setSectionDraft({ name: '', route_ids: [], segment_ids: [] });
  };

  const deleteExclusiveSection = (sectionId) => {
    const nextSections = exclusiveSections.filter((section) => section.id !== sectionId);
    setNetworkSafely({
      ...network,
      exclusive_sections: nextSections,
      interlocking_devices: removeDanglingInterlockingRefs(interlockingDevices, routes, nextSections)
    });
  };

  const getSectionsForRoute = (routeId) => {
    const routeSegmentIds = new Set(getRouteSegmentIds(routeId));
    return exclusiveSections.filter((section) => (
      (section.segment_ids || []).some((segmentId) => routeSegmentIds.has(segmentId))
    ));
  };

  const addInterlockingDevice = () => {
    const routeId = interlockDraft.route_id || routes[0]?.id || '';
    const sectionId = interlockDraft.section_id || getSectionsForRoute(routeId)[0]?.id || '';
    if (!routeById.has(routeId)) {
      alert('連動装置の対象路線を選択してください');
      return;
    }
    if (!exclusiveSections.some((section) => section.id === sectionId)) {
      alert('連動装置が保護する排他区間を選択してください');
      return;
    }
    const sectionsForRoute = getSectionsForRoute(routeId);
    if (!sectionsForRoute.some((section) => section.id === sectionId)) {
      alert('選択した路線は対象排他区間を通過しません');
      return;
    }
    const approachDistance = Number.parseFloat(interlockDraft.approach_distance);
    const stopMargin = Number.parseFloat(interlockDraft.stop_margin);
    if (!Number.isFinite(approachDistance) || approachDistance < 0) {
      alert('判定距離は0以上の数値にしてください');
      return;
    }
    if (!Number.isFinite(stopMargin) || stopMargin < 0) {
      alert('停止余裕は0以上の数値にしてください');
      return;
    }
    const nextId = `I_${Math.max(0, ...interlockingDevices.map((device) => parseInt(String(device.id).replace(/\D/g, ''), 10) || 0)) + 1}`;
    setNetworkSafely({
      ...network,
      interlocking_devices: [
        ...interlockingDevices,
        {
          id: nextId,
          name: interlockDraft.name || nextId,
          route_id: routeId,
          section_id: sectionId,
          approach_distance: approachDistance,
          stop_margin: stopMargin
        }
      ]
    });
    setInterlockDraft({ name: '', route_id: routeId, section_id: sectionId, approach_distance: 150, stop_margin: 0 });
  };

  const deleteInterlockingDevice = (deviceId) => {
    setNetworkSafely({
      ...network,
      interlocking_devices: interlockingDevices.filter((device) => device.id !== deviceId)
    });
  };

  const handleInterlockingDeviceClick = (deviceId) => {
    setSelectedInterlockingDevice(deviceId);
    setSelectedStation(null);
    setConnectionMode(false);
    setFirstStationForConnection(null);
  };

  const formatRouteLegs = (route) => (route.legs || [])
    .map((leg) => `${leg.segment_id}:${leg.from}>${leg.to}`)
    .join(' | ');

  const formatStationIds = (stationIds) => stationIds.map(stationLabel).join(' -> ');

  const routeStartLabel = (route) => stationLabel(routeStationIds(route)[0]);

  const routeEndLabel = (route) => {
    const ids = routeStationIds(route);
    return stationLabel(ids[ids.length - 1]);
  };

  const routeViaLabel = (route) => {
    const ids = routeStationIds(route);
    const via = ids.slice(1, -1);
    return via.length ? via.map(stationLabel).join(', ') : '-';
  };

  const currentTime = simulationTimeFromStates(trainStates) ?? 0;
  const recordingStatusLabel = {
    idle: '未録画',
    recording: `録画中 ${recordingFrameCount}フレーム`,
    encoding: 'GIF生成中',
    ready: `録画完了 ${recordingFrameCount}フレーム`,
    error: recordingError || '録画エラー'
  }[recordingStatus] || '未録画';

  const logByTrain = logEntries.reduce((acc, entry) => {
    const key = entry.train_id || 'unknown';
    if (!acc[key]) acc[key] = [];
    acc[key].push(entry);
    return acc;
  }, {});
  const sectionCandidateSegmentIds = getSectionCandidateSegmentIds(sectionDraft.route_ids || []);
  const interlockRouteId = interlockDraft.route_id || routes[0]?.id || '';
  const interlockSectionOptions = interlockRouteId ? getSectionsForRoute(interlockRouteId) : [];

  return (
    <div className="app" style={{ gridTemplateColumns: `${sidebarWidth}px ${resizerSize}px 1fr` }}>
      <aside className="controls">
        <h3>操作パネル</h3>
        <div style={{ marginBottom: '12px', borderBottom: '2px solid #ddd' }}>
          <button onClick={() => setActiveTab('simulation')} className={activeTab === 'simulation' ? 'active' : ''}>シミュレーション</button>
          <button onClick={() => setActiveTab('routeEditor')} className={activeTab === 'routeEditor' ? 'active' : ''}>経路設計</button>
        </div>

        {activeTab === 'simulation' && (
          <>
            <div className="menu-bar">
              <button
                type="button"
                className={vehicleSettingsOpen ? 'active' : ''}
                onClick={() => setVehicleSettingsOpen((open) => !open)}
              >
                車両パラメータ
              </button>
            </div>
            {vehicleSettingsOpen && (
              <div className="settings-panel">
                <div className="settings-grid">
                  {VEHICLE_PARAM_FIELDS.map((field) => (
                    <label key={field.key} className="settings-field">
                      <span>{field.label}</span>
                      <input
                        type="number"
                        min={field.min}
                        step={field.step}
                        value={vehicleParams[field.key]}
                        onChange={(e) => updateVehicleParam(field.key, e.target.value)}
                      />
                      {field.unit && <small>{field.unit}</small>}
                    </label>
                  ))}
                </div>
                <button type="button" onClick={applyVehicleParamsToTrains}>
                  既存列車に適用
                </button>
              </div>
            )}
            <label>時間ステップ(秒): <input type="number" min="0.05" step="0.05" value={dt} onChange={(e) => setDt(parseFloat(e.target.value))} /></label>
            <label>送信/ログ間隔(秒): <input type="number" min="0.05" step="0.05" value={outputInterval} onChange={(e) => setOutputInterval(parseFloat(e.target.value))} /></label>
            <label>シミュレーション期間(秒): <input type="number" min="1" value={duration} onChange={(e) => setDuration(parseFloat(e.target.value))} /></label>
            <label>描画速度(倍): <input type="number" min="0.1" step="0.1" value={playbackSpeed} onChange={(e) => setPlaybackSpeed(parseFloat(e.target.value) || 1)} /></label>
            {/* ▼▼▼ ここから追加：LLM間隔設定 ▼▼▼ */}
            {simulationMode === 'high_precision_llm' && (
              <label>LLM呼び出し間隔(秒): <input type="number" min="1" step="1" value={llmInterval} onChange={(e) => setLlmInterval(parseInt(e.target.value) || 30)} /></label>
            )}
            {/* ▲▲▲ ここまで追加 ▲▲▲ */}
            {simulationMode === 'follow_idm' && (
              <label>IDM時間ヘッドウェイ T(秒): <input type="number" min="0.1" step="0.1" value={idmTimeHeadway} onChange={(e) => setIdmTimeHeadway(parseFloat(e.target.value) || 1.5)} /></label>
            )}
            {simulationMode === 'headway_control' && (
              <>
                <label>HeadwayTarget(秒): <input type="number" min="0" value={headwayTarget} onChange={(e) => setHeadwayTarget(parseFloat(e.target.value) || 0)} /></label>
                <label>許容幅(±秒): <input type="number" min="0" value={headwayEpsilon} onChange={(e) => setHeadwayEpsilon(parseFloat(e.target.value) || 0)} /></label>
              </>
            )}
            <div className="recording-panel">
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={recordingEnabled}
                  disabled={running || recordingStatus === 'encoding'}
                  onChange={(e) => setRecordingEnabled(e.target.checked)}
                />
                <span>GIF録画</span>
              </label>
              <div className={`recording-status recording-status-${recordingStatus}`}>
                {recordingStatusLabel}
              </div>
              <button
                type="button"
                onClick={downloadRecordingGif}
                disabled={!recordingDownloadUrl || recordingStatus !== 'ready'}
              >
                録画をダウンロード
              </button>
            </div>
            <div className="buttons">
              {!running ? <button onClick={start}>シミュレーション開始</button> : <button onClick={stop}>停止</button>}
              <button onClick={downloadCSV}>CSV をダウンロード</button>
            </div>
            <TrainEditor
              trains={trains}
              setTrains={setTrains}
              routes={routes}
              simulationMode={simulationMode}
              setSimulationMode={setSimulationMode}
              vehicleParams={sanitizeVehicleParams(vehicleParams)}
            />
          </>
        )}

        {activeTab === 'routeEditor' && (
          <>
            <div style={{ padding: 8, background: '#fff', border: '1px solid #ddd' }}>
              <h4 style={{ margin: '0 0 8px 0' }}>経路設計</h4>
              <label>
                設計名:{' '}
                <input
                  value={routeDesignName}
                  placeholder="未入力なら実行日時"
                  onChange={(e) => setRouteDesignName(e.target.value)}
                />
              </label>
              <label>
                保存済み設計:{' '}
                <select
                  value={selectedRouteDesignId}
                  onChange={(e) => setSelectedRouteDesignId(e.target.value)}
                  disabled={routeDesignHistory.length === 0}
                >
                  {routeDesignHistory.length === 0 ? (
                    <option value="">保存済み設計なし</option>
                  ) : (
                    routeDesignHistory.map((entry) => (
                      <option key={entry.id} value={entry.id}>
                        {entry.name}{entry.savedAt ? ` / ${entry.savedAt}` : ''}
                      </option>
                    ))
                  )}
                </select>
              </label>
              <button onClick={loadRouteDesign} disabled={routeDesignHistory.length === 0}>
                呼び出す
              </button>
            </div>

            <div style={{ marginTop: 12, padding: 8, background: '#fff', border: '1px solid #ddd' }}>
              <h4 style={{ margin: '0 0 8px 0' }}>地点追加</h4>
              <div style={{ fontSize: 12, color: '#555' }}>
                キャンバス左上のボタンで、表示中キャンバスの中心に駅または通過点を追加します。
              </div>
            </div>

            {selectedStation && (
              <div style={{ marginTop: 12, padding: 8, background: '#fff', border: '1px solid #ddd' }}>
                <h4>地点情報: {stationById.get(selectedStation)?.name}</h4>
                <label>名称: <input value={stationById.get(selectedStation)?.name || ''} onChange={(e) => updateStation(selectedStation, { name: e.target.value })} /></label>
                <label>
                  種類:{' '}
                  <select
                    value={stationById.get(selectedStation)?.kind || 'station'}
                    onChange={(e) => updateStation(selectedStation, {
                      kind: e.target.value,
                      stop_time: e.target.value === 'waypoint' ? 0 : (stationById.get(selectedStation)?.stop_time ?? 0),
                      length: e.target.value === 'waypoint' ? 0 : (stationById.get(selectedStation)?.length ?? 0)
                    })}
                  >
                    <option value="station">駅</option>
                    <option value="waypoint">通過点</option>
                  </select>
                </label>
                {(stationById.get(selectedStation)?.kind || 'station') === 'station' && (
                  <>
                    <label>停車時間: <input type="number" value={stationById.get(selectedStation)?.stop_time ?? 0} onChange={(e) => updateStation(selectedStation, { stop_time: parseFloat(e.target.value) || 0 })} /></label>
                    <label>駅長さ: <input type="number" value={stationById.get(selectedStation)?.length ?? 0} onChange={(e) => updateStation(selectedStation, { length: parseFloat(e.target.value) || 0 })} /></label>
                  </>
                )}
                <button onClick={() => deleteStationFromRoute(selectedStation)}>地点を削除</button>
              </div>
            )}

            <div style={{ marginTop: 12, padding: 8, background: '#fff', border: '1px solid #ddd' }}>
              <h4>路線 作成</h4>
              {!routeEditing ? (
                <button onClick={startRouteEditing}>路線作成を開始</button>
              ) : (
                <>
                  <label>路線名: <input value={routeDraft.name} onChange={(e) => setRouteDraft({ ...routeDraft, name: e.target.value })} /></label>
                  <div style={{ fontSize: 12, color: '#555', margin: '4px 0 8px' }}>
                    キャンバス上の地点を通る順番にクリックしてください。起点を最後にもう一度クリックすると円環路線になります。
                  </div>
                  <div style={{ padding: 6, background: '#f5f8ff', border: '1px solid #c7d7f2', marginBottom: 8 }}>
                    <strong>選択中の経路:</strong>{' '}
                    {(routeDraft.station_ids || []).length > 0
                      ? formatStationIds(routeDraft.station_ids)
                      : '地点をクリックしてください'}
                  </div>
                  <div className="buttons">
                    <button onClick={addRoute} disabled={(routeDraft.station_ids || []).length < 2}>路線を保存</button>
                    <button onClick={undoRouteStation} disabled={(routeDraft.station_ids || []).length === 0}>1つ戻す</button>
                    <button onClick={cancelRouteEditing}>キャンセル</button>
                  </div>
                </>
              )}
            </div>

            <div style={{ marginTop: 12, padding: 8, background: '#fff', border: '1px solid #ddd' }}>
              <h4>排他区間 作成</h4>
              <label>名称: <input value={sectionDraft.name} onChange={(e) => setSectionDraft({ ...sectionDraft, name: e.target.value })} /></label>
              <div style={{ margin: '8px 0' }}>
                <div style={{ fontSize: 12, color: '#555', marginBottom: 4 }}>対象路線:</div>
                {routes.length === 0 ? (
                  <div style={{ fontSize: 12, color: '#777' }}>先に路線を作成してください。</div>
                ) : (
                  routes.map((route) => (
                    <label key={route.id} style={{ display: 'block', marginBottom: 4 }}>
                      <input
                        type="checkbox"
                        checked={(sectionDraft.route_ids || []).includes(route.id)}
                        onChange={(e) => setSectionRoutes(route.id, e.target.checked)}
                      />{' '}
                      {routeLabel(route.id)}
                    </label>
                  ))
                )}
              </div>
              <div style={{ margin: '8px 0' }}>
                <div style={{ fontSize: 12, color: '#555', marginBottom: 4 }}>
                  対象線路:
                </div>
                {(sectionDraft.route_ids || []).length === 0 ? (
                  <div style={{ fontSize: 12, color: '#777' }}>対象路線を選択すると候補が表示されます。</div>
                ) : sectionCandidateSegmentIds.length === 0 ? (
                  <div style={{ fontSize: 12, color: '#b33a3a' }}>
                    選択した路線間で共通する線路がありません。
                  </div>
                ) : (
                  sectionCandidateSegmentIds.map((segmentId) => (
                    <label key={segmentId} style={{ display: 'block', marginBottom: 4 }}>
                      <input
                        type="checkbox"
                        checked={(sectionDraft.segment_ids || []).includes(segmentId)}
                        onChange={(e) => setSectionSegment(segmentId, e.target.checked)}
                      />{' '}
                      {segmentLabel(segmentId)}
                    </label>
                  ))
                )}
              </div>
              <div style={{ fontSize: 12, color: '#555', margin: '4px 0 8px' }}>
                進入許可は先に許可を取得した列車を優先します。対象路線は排他区間の候補指定に使います。
              </div>
              <button onClick={addExclusiveSection}>排他区間を追加</button>
            </div>

            <div style={{ marginTop: 12, padding: 8, background: '#fff', border: '1px solid #ddd' }}>
              <h4>連動装置 作成</h4>
              <label>
                名称:{' '}
                <input value={interlockDraft.name} onChange={(e) => setInterlockDraft({ ...interlockDraft, name: e.target.value })} />
              </label>
              <label>
                対象路線:{' '}
                <select
                  value={interlockRouteId}
                  onChange={(e) => {
                    const nextRouteId = e.target.value;
                    const nextSections = getSectionsForRoute(nextRouteId);
                    setInterlockDraft({
                      ...interlockDraft,
                      route_id: nextRouteId,
                      section_id: nextSections[0]?.id || ''
                    });
                  }}
                >
                  {routes.map((route) => (
                    <option key={route.id} value={route.id}>{routeLabel(route.id)}</option>
                  ))}
                </select>
              </label>
              <label>
                保護する排他区間:{' '}
                <select
                  value={interlockDraft.section_id || interlockSectionOptions[0]?.id || ''}
                  onChange={(e) => setInterlockDraft({ ...interlockDraft, section_id: e.target.value })}
                  disabled={interlockSectionOptions.length === 0}
                >
                  {interlockSectionOptions.map((section) => (
                    <option key={section.id} value={section.id}>{section.name || section.id}</option>
                  ))}
                </select>
              </label>
              <label>
                判定距離(m):{' '}
                <input
                  type="number"
                  min="0"
                  value={interlockDraft.approach_distance}
                  onChange={(e) => setInterlockDraft({ ...interlockDraft, approach_distance: parseFloat(e.target.value) || 0 })}
                />
              </label>
              <label>
                停止余裕(m):{' '}
                <input
                  type="number"
                  min="0"
                  value={interlockDraft.stop_margin}
                  onChange={(e) => setInterlockDraft({ ...interlockDraft, stop_margin: parseFloat(e.target.value) || 0 })}
                />
              </label>
              <div style={{ fontSize: 12, color: '#555', margin: '4px 0 8px' }}>
                判定距離は、対象排他区間の入口から何m手前で進入許可を予約するかを表します。
              </div>
              <button onClick={addInterlockingDevice} disabled={routes.length === 0 || exclusiveSections.length === 0 || interlockSectionOptions.length === 0}>
                連動装置を追加
              </button>
            </div>
          </>
        )}
      </aside>

      <div
        className="resizer vertical"
        onMouseDown={(e) => {
          e.preventDefault();
          resizeModeRef.current = 'sidebar';
          document.body.style.cursor = 'col-resize';
          document.body.style.userSelect = 'none';
        }}
      />

      <main className="main-area" style={{ gridTemplateRows: `minmax(220px, 1fr) ${infoPaneHeight}px` }}>
        <div className="canvas-pane">
          <div className="canvas-wrapper" ref={canvasContainerRef}>
            {activeTab === 'simulation' && network && (
              <Canvas
                network={network}
                trainStates={trainStates}
                width={canvasSize.width}
                height={canvasSize.height}
                stageRef={simulationStageRef}
              />
            )}
            {activeTab === 'routeEditor' && network && (
              <RouteEditorCanvas
                network={network}
                setNetwork={setNetworkSafely}
                selectedStation={selectedStation}
                setSelectedStation={setSelectedStation}
                connectionMode={connectionMode}
                setConnectionMode={setConnectionMode}
                firstStationForConnection={firstStationForConnection}
                setFirstStationForConnection={setFirstStationForConnection}
                routeEditing={routeEditing}
                routeDraftStationIds={routeDraft.station_ids || []}
                onRouteStationClick={handleRouteStationClick}
                selectedInterlockingDevice={selectedInterlockingDevice}
                onInterlockingDeviceClick={handleInterlockingDeviceClick}
                width={canvasSize.width}
                height={canvasSize.height}
              />
            )}
            {activeTab === 'simulation' && (
              <div className="canvas-overlay">
                <div className="overlay-row">Time: {currentTime.toFixed(1)}s</div>
                {trainStates.map((state) => (
                  <div key={state.train_id} className="overlay-row">
                    {state.train_id}: {state.status || '-'} {state.block_id ? `(${state.block_id})` : ''}
                  </div>
                ))}
              </div>
            )}

           {/* ▼▼▼ 修正: LLMステータスバナー ▼▼▼ */}
            {activeTab === 'simulation' && llmStatus !== 'idle' && (
              <div style={{
                position: 'absolute',
                top: 16,
                right: 16,
                // ステータスに応じて背景色を切り替え (緑 / 赤 / 黒)
                backgroundColor: llmStatus === 'success' ? 'rgba(40, 167, 69, 0.9)' :
                                 llmStatus === 'error'   ? 'rgba(220, 53, 69, 0.9)' :
                                 'rgba(0, 0, 0, 0.75)',
                color: '#fff',
                padding: '8px 16px',
                borderRadius: '8px',
                fontWeight: 'bold',
                zIndex: 1000,
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                boxShadow: '0 4px 6px rgba(0,0,0,0.3)',
                transition: 'background-color 0.3s ease'
              }}>
                {llmStatus === 'thinking' && <><span style={{ fontSize: '1.2em' }}>🧠</span> LLM推論中...</>}
                {llmStatus === 'success'  && <><span style={{ fontSize: '1.2em' }}>✅</span> 推論成功</>}
                {llmStatus === 'error'    && <><span style={{ fontSize: '1.2em' }}>⚠️</span> APIエラー</>}
              </div>
            )}
            {/* ▲▲▲ 修正ここまで ▲▲▲ */}


            
          </div>
        </div>

        <div className="info-pane">
          <div
            className="resizer horizontal"
            onMouseDown={(e) => {
              e.preventDefault();
              resizeModeRef.current = 'info';
              document.body.style.cursor = 'row-resize';
              document.body.style.userSelect = 'none';
            }}
          />
          <div className="tab-header">
            <span className="tab-title">{activeTab === 'simulation' ? 'ログ' : '詳細テーブル'}</span>
          </div>
          <div className="tab-content">
            {activeTab === 'simulation' && (
              <div className="log-table-wrapper">
                {Object.keys(logByTrain).length === 0 ? (
                  <div className="tab-placeholder">データがありません。</div>
                ) : (
                  <div className="log-train-row">
                    {Object.entries(logByTrain).map(([trainId, rows]) => (
                      <div className="log-train-card" key={trainId}>
                        <div className="log-train-title">{trainId}</div>
                        <table className="log-table">
                          <thead>
                            <tr>
                              <th>time</th>
                              <th>route</th>
                              <th>segment</th>
                              <th>distance</th>
                              <th>speed</th>
                              <th>status</th>
                              <th>reason</th>
                            </tr>
                          </thead>
                          <tbody>
                            {rows.map((row, idx) => (
                              <tr key={`${trainId}-${row.time}-${idx}`}>
                                <td>{Number.isFinite(row.time) ? row.time.toFixed(1) : '-'}</td>
                                <td>{row.route_id || '-'}</td>
                                <td>{Array.isArray(row.segment_ids) && row.segment_ids.length > 0 ? row.segment_ids.join(', ') : (row.segment_id || '-')}</td>
                                <td>{Number.isFinite(row.route_distance) ? row.route_distance.toFixed(1) : '-'}</td>
                                <td>{Number.isFinite(row.speed) ? row.speed.toFixed(1) : '-'}</td>
                                <td>{row.status || '-'}</td>
                                <td>{row.wait_reason ? `${row.wait_reason} ${row.block_id || ''}` : row.control_reason ? `${row.control_reason} ${row.control_block_id || ''}` : '-'}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {activeTab === 'routeEditor' && (
              <div className="route-tables">
                <div className="route-table-card">
                  <div className="route-table-title">線路</div>
                  <table className="log-table">
                    <thead>
                      <tr>
                        <th>ID</th>
                        <th>開始地点</th>
                        <th>終了地点</th>
                        <th>長さ(m)</th>
                        <th>勾配(‰)</th>
                        <th>曲線半径(m)</th>
                        <th>制限速度(km/h)</th>
                        <th>走行時分(秒)</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {segments.map((segment) => {
                        const lengthValue = Number.isFinite(segment.length) ? segment.length : getSegmentDistance(segment);
                        return (
                          <tr key={segment.id}>
                            <td>{segment.id}</td>
                            <td>{stationLabel(segment.start)}</td>
                            <td>{stationLabel(segment.end)}</td>
                            <td>
                              <input type="number" value={Number.isFinite(lengthValue) ? lengthValue : ''} onChange={(e) => updateSegmentLength(segment.id, parseFloat(e.target.value))} style={{ width: 80 }} />
                            </td>
                            {/* --- ここから追加 --- */}
                            <td>
                              <input type="number" value={Number.isFinite(segment.gradient) ? segment.gradient : ''} onChange={(e) => updateSegmentProperty(segment.id, 'gradient', parseFloat(e.target.value))} style={{ width: 60 }} />
                            </td>
                            <td>
                              <input type="number" value={Number.isFinite(segment.curve_radius) ? segment.curve_radius : ''} onChange={(e) => updateSegmentProperty(segment.id, 'curve_radius', parseFloat(e.target.value))} style={{ width: 60 }} />
                            </td>
                            <td>
                              <input type="number" value={Number.isFinite(segment.speed_limit) ? segment.speed_limit : ''} onChange={(e) => updateSegmentProperty(segment.id, 'speed_limit', parseFloat(e.target.value))} style={{ width: 60 }} />
                            </td>
                            {/* --- ここまで追加 --- */}
                            <td>{Number.isFinite(segment.travel_time) ? segment.travel_time : '-'}</td>
                            <td><button onClick={() => deleteSegmentFromRoute(segment.id)}>削除</button></td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>

                <div className="route-table-card">
                  <div className="route-table-title">路線</div>
                  <table className="log-table">
                    <thead>
                      <tr><th>ID</th><th>路線名</th><th>起点</th><th>経由</th><th>終点</th><th>経路</th><th></th></tr>
                    </thead>
                    <tbody>
                      {routes.map((route) => {
                        const stationIds = routeStationIds(route);
                        return (
                          <tr key={route.id}>
                            <td>{route.id}</td>
                            <td>{route.name}</td>
                            <td>{routeStartLabel(route)}</td>
                            <td>{routeViaLabel(route)}</td>
                            <td>{routeEndLabel(route)}</td>
                            <td title={formatRouteLegs(route)}>{formatStationIds(stationIds)}</td>
                            <td><button onClick={() => deleteRoute(route.id)}>削除</button></td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>

                <div className="route-table-card">
                  <div className="route-table-title">排他区間</div>
                  <table className="log-table">
                    <thead>
                      <tr><th>ID</th><th>名称</th><th>対象線路</th><th>対象路線</th><th></th></tr>
                    </thead>
                    <tbody>
                      {exclusiveSections.map((section) => (
                        <tr key={section.id}>
                          <td>{section.id}</td>
                          <td>{section.name}</td>
                          <td>{(section.segment_ids || []).map(segmentLabel).join(', ')}</td>
                          <td>{(section.priority_route_ids || []).map(routeLabel).join(', ')}</td>
                          <td><button onClick={() => deleteExclusiveSection(section.id)}>削除</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div className="route-table-card">
                  <div className="route-table-title">連動装置</div>
                  <table className="log-table">
                    <thead>
                      <tr><th>状態</th><th>ID</th><th>名称</th><th>対象路線</th><th>保護区間</th><th>判定距離(m)</th><th>停止余裕(m)</th><th></th></tr>
                    </thead>
                    <tbody>
                      {interlockingDevices.map((device) => {
                        const isSelected = selectedInterlockingDevice === device.id;
                        return (
                          <tr
                            key={device.id}
                            ref={isSelected ? selectedInterlockingRowRef : null}
                            className={isSelected ? 'selected-table-row' : ''}
                            onClick={() => handleInterlockingDeviceClick(device.id)}
                          >
                            <td>{isSelected ? '選択中' : '-'}</td>
                            <td>{device.id}</td>
                            <td>{device.name}</td>
                            <td>{routeLabel(device.route_id)}</td>
                            <td>{exclusiveSections.find((section) => section.id === device.section_id)?.name || device.section_id}</td>
                            <td>{Number.isFinite(device.approach_distance) ? device.approach_distance : '-'}</td>
                            <td>{Number.isFinite(device.stop_margin) ? device.stop_margin : 0}</td>
                            <td><button onClick={(e) => { e.stopPropagation(); deleteInterlockingDevice(device.id); }}>削除</button></td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
