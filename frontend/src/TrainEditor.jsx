import React, { useEffect, useState } from 'react';

const PRESET_KEY = 'train_presets_v2';

function loadPresets() {
  try {
    const raw = localStorage.getItem(PRESET_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function savePresets(presets) {
  localStorage.setItem(PRESET_KEY, JSON.stringify(presets));
}

export default function TrainEditor({
  trains,
  setTrains,
  routes = [],
  simulationMode = 'low_precision',
  setSimulationMode,
  vehicleParams = {}
}) {
  const defaultMaxSpeed = Number.isFinite(vehicleParams.max_speed) && vehicleParams.max_speed > 0 ? vehicleParams.max_speed : 60;
  const defaultTrainLength = Number.isFinite(vehicleParams.length) && vehicleParams.length > 0 ? vehicleParams.length : 200;
  const defaultAccel = Number.isFinite(vehicleParams.accel) && vehicleParams.accel > 0 ? vehicleParams.accel : 3.2;
  const defaultDecel = Number.isFinite(vehicleParams.decel) && vehicleParams.decel > 0 ? vehicleParams.decel : 4.0;
  // --- 以下2行を追加 ---
  const defaultWeight = Number.isFinite(vehicleParams.weight) && vehicleParams.weight > 0 ? vehicleParams.weight : 30.0;
  const defaultFactorOfInertia = Number.isFinite(vehicleParams.factor_of_inertia) && vehicleParams.factor_of_inertia >= 1.0 ? vehicleParams.factor_of_inertia : 1.1;
  // -------------------
  const lowPrecisionAccel = Number.isFinite(vehicleParams.low_precision_accel) && vehicleParams.low_precision_accel > 0 ? vehicleParams.low_precision_accel : 3.0;
  const lowPrecisionDecel = Number.isFinite(vehicleParams.low_precision_decel) && vehicleParams.low_precision_decel > 0 ? vehicleParams.low_precision_decel : 4.0;
  const [presetName, setPresetName] = useState('');
  const [presets, setPresets] = useState([]);
  const firstRouteId = routes[0]?.id || '';
  const validRouteIds = new Set(routes.map((route) => route.id));

  useEffect(() => {
    setPresets(loadPresets());
  }, []);

  useEffect(() => {
    if (!firstRouteId) return;
    let changed = false;
    const nextTrains = trains.map((train) => {
      if (validRouteIds.has(train.route_id)) return train;
      changed = true;
      return { ...train, route_id: firstRouteId };
    });
    if (changed) {
      setTrains(nextTrains);
    }
  }, [firstRouteId, routes, trains, setTrains]);

  const addTrain = () => {
    setTrains([
      ...trains,
      {
        train_id: `T${trains.length + 1}`,
        route_id: firstRouteId,
        max_speed: defaultMaxSpeed,
        accel: defaultAccel,
        decel: defaultDecel,
        length: defaultTrainLength,
        weight: defaultWeight,
        factor_of_inertia: defaultFactorOfInertia,
        start_time: 0
      }
    ]);
  };

  const update = (idx, key, val) => {
    setTrains(trains.map((t, i) => (i === idx ? { ...t, [key]: val } : t)));
  };

  const remove = (idx) => {
    setTrains(trains.filter((_, i) => i !== idx));
  };

  const savePreset = () => {
    if (!presetName) return;
    const next = [...presets.filter((x) => x.name !== presetName), { name: presetName, trains }];
    setPresets(next);
    savePresets(next);
    setPresetName('');
  };

  const loadPreset = (preset) => {
    const nextTrains = (preset.trains || []).map((t, idx) => ({
      train_id: t.train_id || `T${idx + 1}`,
      route_id: t.route_id || firstRouteId,
      max_speed: Number.isFinite(t.max_speed) ? t.max_speed : defaultMaxSpeed,
      accel: Number.isFinite(t.accel) ? t.accel : defaultAccel,
      decel: Number.isFinite(t.decel) ? t.decel : defaultDecel,
      length: Number.isFinite(t.length) && t.length > 0 ? t.length : defaultTrainLength,
      weight: Number.isFinite(t.weight) && t.weight > 0 ? t.weight : defaultWeight,
      factor_of_inertia: Number.isFinite(t.factor_of_inertia) && t.factor_of_inertia >= 1.0 ? t.factor_of_inertia : defaultFactorOfInertia,
      start_time: Number.isFinite(t.start_time) && t.start_time >= 0 ? t.start_time : 0
    }));
    setTrains(nextTrains);
  };

  const deletePreset = (name) => {
    const next = presets.filter((p) => p.name !== name);
    setPresets(next);
    savePresets(next);
  };

  return (
    <div>
      <h4>列車</h4>

      <div style={{ marginBottom: '12px', padding: '8px', backgroundColor: '#f5f5f5', borderRadius: '4px' }}>
        <label style={{ display: 'block', marginBottom: '6px' }}>
          <strong>シミュレーションモード:</strong>
        </label>
        <label style={{ marginRight: '16px' }}>
          <input
            type="radio"
            name="simMode"
            value="low_precision"
            checked={simulationMode === 'low_precision'}
            onChange={(e) => setSimulationMode(e.target.value)}
          />
          低精度（加速度 {lowPrecisionAccel}、減速度 {lowPrecisionDecel} km/h/s）
        </label>
        <label style={{ marginRight: '16px' }}>
          <input
            type="radio"
            name="simMode"
            value="high_precision"
            checked={simulationMode === 'high_precision'}
            onChange={(e) => setSimulationMode(e.target.value)}
          />
          高精度
        </label>
        {/* ========================================== */}
        {/* ▼▼▼ ここから追加：高精度＋LLMモード ▼▼▼ */}
        {/* ========================================== */}
        <label style={{ marginRight: '16px' }}>
          <input
            type="radio"
            name="simMode"
            value="high_precision_llm"
            checked={simulationMode === 'high_precision_llm'}
            onChange={(e) => setSimulationMode(e.target.value)}
          />
          高精度＋LLM
        </label>
        {/* ========================================== */}
        {/* ▲▲▲ ここまで追加 ▲▲▲ */}
        {/* ========================================== */}
        <label style={{ marginRight: '16px' }}>
          <input
            type="radio"
            name="simMode"
            value="follow_idm"
            checked={simulationMode === 'follow_idm'}
            onChange={(e) => setSimulationMode(e.target.value)}
          />
          追従IDM
        </label>
        <label>
          <input
            type="radio"
            name="simMode"
            value="headway_control"
            checked={simulationMode === 'headway_control'}
            onChange={(e) => setSimulationMode(e.target.value)}
          />
          到着間隔制御
        </label>
      </div>

      <button onClick={addTrain} disabled={!firstRouteId}>列車を追加</button>
      {!firstRouteId && <div style={{ fontSize: 12, color: '#a44', marginTop: 4 }}>先に route を作成してください。</div>}

      <div style={{ marginTop: '8px', marginBottom: '12px' }}>
        <input
          placeholder="プリセット名"
          value={presetName}
          onChange={(e) => setPresetName(e.target.value)}
        />
        <button onClick={savePreset} style={{ marginLeft: '8px' }}>
          プリセットを保存
        </button>
      </div>

      {presets.length > 0 && (
        <div style={{ marginBottom: '12px' }}>
          <strong>保存済みプリセット:</strong>
          <ul>
            {presets.map((p) => (
              <li key={p.name}>
                {p.name}
                <button onClick={() => loadPreset(p)} style={{ marginLeft: '8px' }}>読み込み</button>
                <button onClick={() => deletePreset(p.name)} style={{ marginLeft: '6px' }}>削除</button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {trains.map((t, i) => (
        <div key={i} style={{ border: '1px solid #ddd', padding: '8px', marginTop: '8px' }}>
          <label>
            ID:{' '}
            <input value={t.train_id} onChange={(e) => update(i, 'train_id', e.target.value)} />
          </label>
          <label>
            Route:{' '}
            <select value={validRouteIds.has(t.route_id) ? t.route_id : firstRouteId} onChange={(e) => update(i, 'route_id', e.target.value)}>
              {routes.map((route) => (
                <option key={route.id} value={route.id}>{route.name || route.id}</option>
              ))}
            </select>
          </label>
          <label>
            最大速度(km/h):{' '}
            <input type="number" value={t.max_speed} onChange={(e) => update(i, 'max_speed', parseFloat(e.target.value))} />
          </label>
          <label>
            列車長さ(m):{' '}
            <input type="number" value={t.length ?? defaultTrainLength} onChange={(e) => update(i, 'length', parseFloat(e.target.value))} />
          </label>
          <label>
            車両重量(t):{' '}
            <input type="number" value={t.weight ?? defaultWeight} onChange={(e) => update(i, 'weight', parseFloat(e.target.value))} />
          </label>
          <label>
            慣性係数:{' '}
            <input type="number" value={t.factor_of_inertia ?? defaultFactorOfInertia} onChange={(e) => update(i, 'factor_of_inertia', parseFloat(e.target.value))} />
          </label>
          <label>
            加速度(km/h/s):{' '}
            <input
              type="number"
              value={t.accel}
              onChange={(e) => update(i, 'accel', parseFloat(e.target.value))}
              disabled={simulationMode === 'low_precision'}
              title={simulationMode === 'low_precision' ? `低精度モードでは ${lowPrecisionAccel} を使用` : ''}
            />
          </label>
          <label>
            減速度(km/h/s):{' '}
            <input
              type="number"
              value={t.decel}
              onChange={(e) => update(i, 'decel', parseFloat(e.target.value))}
              disabled={simulationMode === 'low_precision'}
              title={simulationMode === 'low_precision' ? `低精度モードでは ${lowPrecisionDecel} を使用` : ''}
            />
          </label>
          <label>
            Start time (s):{' '}
            <input type="number" value={t.start_time ?? 0} onChange={(e) => update(i, 'start_time', parseFloat(e.target.value))} />
          </label>
          <button onClick={() => remove(i)}>削除</button>
        </div>
      ))}
    </div>
  );
}
