import React from 'react';
import { Stage, Layer, Line, Circle, Text, Group, Rect } from 'react-konva';

/**
 * Canvas コンポーネント：ネットワークと列車を Konva で描画
 * - ネットワーク：駅（青い円）と線路（灰色の線）を表示
 * - 列車：各列車を色分けした円で表示（速度と ID はラベルで表示）
 * 要件：列車は必ずエディタモードで設定した線路の上を動く
 */
export default function Canvas({ network, trainStates, width = 900, height = 400, stageRef = null }) {
  
  // ネットワークが読み込まれるまで待機
  if (!network) return <div>ネットワークを読み込み中...</div>;

  const stations = Array.isArray(network.stations) ? network.stations : [];
  const segments = Array.isArray(network.segments) ? network.segments : [];
  const routes = Array.isArray(network.routes) ? network.routes : [];
  const interlockingDevices = Array.isArray(network.interlocking_devices) ? network.interlocking_devices : [];
  const exclusiveSegmentIds = new Set(
    (network.exclusive_sections || []).flatMap((section) => section.segment_ids || [])
  );

  const stageScaleRef = React.useRef(1);
  const stagePosRef = React.useRef({ x: 0, y: 0 });
  const isPanningRef = React.useRef(false);
  const lastPointerRef = React.useRef(null);
  const [stageScale, setStageScale] = React.useState(1);
  const [stagePos, setStagePos] = React.useState({ x: 0, y: 0 });

  const handleWheel = (e) => {
    e.evt.preventDefault();
    const scaleBy = 1.05;
    const oldScale = stageScaleRef.current;
    const pointer = e.target.getStage()?.getPointerPosition();
    if (!pointer) return;
    const mousePointTo = {
      x: (pointer.x - stagePosRef.current.x) / oldScale,
      y: (pointer.y - stagePosRef.current.y) / oldScale
    };

    const direction = e.evt.deltaY > 0 ? -1 : 1;
    let newScale = direction > 0 ? oldScale * scaleBy : oldScale / scaleBy;
    newScale = Math.max(0.2, Math.min(5, newScale));

    const newPos = {
      x: pointer.x - mousePointTo.x * newScale,
      y: pointer.y - mousePointTo.y * newScale
    };

    stageScaleRef.current = newScale;
    stagePosRef.current = newPos;
    setStageScale(newScale);
    setStagePos(newPos);
  };

  const handleMouseDown = (e) => {
    if (e.evt.button !== 1) return;
    e.evt.preventDefault();
    isPanningRef.current = true;
    const stage = e.target.getStage();
    const pointer = stage?.getPointerPosition();
    if (pointer) {
      lastPointerRef.current = pointer;
    }
    const container = stage?.container?.();
    if (container) {
      container.style.cursor = 'grabbing';
    }
  };

  const handleMouseUp = (e) => {
    if (!isPanningRef.current) return;
    isPanningRef.current = false;
    lastPointerRef.current = null;
    const stage = e.target.getStage();
    const container = stage?.container?.();
    if (container) {
      container.style.cursor = 'default';
    }
  };

  const handleMouseMove = (e) => {
    if (!isPanningRef.current) return;
    const stage = e.target.getStage();
    const pointer = stage?.getPointerPosition();
    const last = lastPointerRef.current;
    if (!pointer || !last) return;
    const dx = pointer.x - last.x;
    const dy = pointer.y - last.y;
    const newPos = {
      x: stagePosRef.current.x + dx,
      y: stagePosRef.current.y + dy
    };
    stagePosRef.current = newPos;
    setStagePos(newPos);
    lastPointerRef.current = pointer;
  };

  // 駅 ID から駅オブジェクトへのマッピング
  const stationMap = Object.fromEntries(
    stations.map((s) => [s.id, s])
  );
  const segmentMap = Object.fromEntries(
    segments.map((segment) => [segment.id, segment])
  );
  const sectionMap = Object.fromEntries(
    (network.exclusive_sections || []).map((section) => [section.id, section])
  );

  const getSegmentDistance = (segment) => {
    if (Number.isFinite(segment?.length) && segment.length > 0) return segment.length;
    const start = stationMap[segment?.start];
    const end = stationMap[segment?.end];
    if (!start || !end) return 0;
    return Math.hypot(end.x - start.x, end.y - start.y);
  };

  const pointOnRoute = (route, targetDistance) => {
    let accumulated = 0;
    for (const leg of route?.legs || []) {
      const segment = segmentMap[leg.segment_id];
      const start = stationMap[leg.from];
      const end = stationMap[leg.to];
      if (!segment || !start || !end) continue;
      const length = getSegmentDistance(segment);
      if (targetDistance <= accumulated + length || leg === route.legs[route.legs.length - 1]) {
        const ratio = length > 0 ? Math.max(0, Math.min(1, (targetDistance - accumulated) / length)) : 0;
        const dx = end.x - start.x;
        const dy = end.y - start.y;
        const visualLength = Math.hypot(dx, dy);
        return {
          x: start.x + (end.x - start.x) * ratio,
          y: start.y + (end.y - start.y) * ratio,
          nx: visualLength > 0 ? -dy / visualLength : 0,
          ny: visualLength > 0 ? dx / visualLength : -1
        };
      }
      accumulated += length;
    }
    return null;
  };

  const interlockingDevicePoint = (device) => {
    const route = routes.find((r) => r.id === device.route_id);
    const section = sectionMap[device.section_id];
    if (!route || !section) return null;
    let accumulated = 0;
    for (const leg of route.legs || []) {
      if ((section.segment_ids || []).includes(leg.segment_id)) {
        return pointOnRoute(route, Math.max(0, accumulated - (Number(device.approach_distance) || 0)));
      }
      accumulated += getSegmentDistance(segmentMap[leg.segment_id]);
    }
    return null;
  };

  const layoutInterlockingDevices = () => {
    const groups = new Map();
    for (const device of interlockingDevices) {
      const point = interlockingDevicePoint(device);
      if (!point) continue;
      const key = `${Math.round(point.x * 10) / 10}:${Math.round(point.y * 10) / 10}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push({ device, point });
    }

    const laidOut = [];
    for (const group of groups.values()) {
      const nx = group[0].point.nx ?? 0;
      const ny = group[0].point.ny ?? -1;
      group.forEach((item, index) => {
        const offset = group.length > 1 ? (index - (group.length - 1) / 2) * 30 : 0;
        laidOut.push({
          ...item,
          x: item.point.x + nx * offset,
          y: item.point.y + ny * offset,
          anchorX: item.point.x,
          anchorY: item.point.y,
          offsetDistance: Math.abs(offset)
        });
      });
    }
    return laidOut;
  };

  /**
   * 線路を描画：各セグメントを駅座標を結ぶ線として表示
   */
  const lines = segments.map((seg) => {
    const start = stationMap[seg.start];
    const end = stationMap[seg.end];
    if (!start || !end) {
      return null;
    }
    const labelX = (start.x + end.x) / 2 - 10;
    const labelY = (start.y + end.y) / 2 - 18;
    return (
      <React.Fragment key={seg.id}>
        <Line
          points={[start.x, start.y, end.x, end.y]}
          stroke={exclusiveSegmentIds.has(seg.id) ? '#b33a3a' : '#444'}
          strokeWidth={exclusiveSegmentIds.has(seg.id) ? 8 : 6}
          lineCap="round"
        />
        <Line
          points={[start.x, start.y, end.x, end.y]}
          stroke="#888"
          strokeWidth={2}
          dash={[6, 6]}
          lineCap="round"
        />
        <Text
          x={labelX}
          y={labelY}
          text={seg.id}
          fontSize={10}
          fill={exclusiveSegmentIds.has(seg.id) ? '#8d2525' : '#555'}
        />
      </React.Fragment>
    );
  });

  /**
   * 駅ノードを描画：円と駅名ラベル
   */
  const stationNodes = stations.map((s) => {
    const isWaypoint = s.kind === 'waypoint';
    return (
      <React.Fragment key={s.id}>
        <Group x={s.x} y={s.y}>
          {isWaypoint ? (
            <>
              <Circle x={0} y={0} radius={8} fill="#fff5cc" stroke="#9a6b00" strokeWidth={2} />
              <Line points={[0, -11, 11, 0, 0, 11, -11, 0]} fill="#ffe08a" stroke="#9a6b00" strokeWidth={1} closed />
            </>
          ) : (
            <>
              <Rect x={-10} y={-12} width={20} height={16} fill="#f2f2f2" stroke="#444" strokeWidth={1} cornerRadius={2} />
              <Line points={[-12, -12, 0, -20, 12, -12]} fill="#c75c5c" stroke="#7a2e2e" strokeWidth={1} closed />
              <Rect x={-3} y={-4} width={6} height={8} fill="#9cc3d5" stroke="#4b6a7a" strokeWidth={0.5} />
            </>
          )}
        </Group>
        <Text x={s.x + 14} y={s.y - 8} text={s.name} fontSize={12} fill={isWaypoint ? '#795000' : '#333'} />
      </React.Fragment>
    );
  });

  const interlockingMarks = layoutInterlockingDevices().map((mark) => {
    const { device } = mark;
    return (
      <Group key={device.id} x={mark.x} y={mark.y}>
        {mark.offsetDistance > 0 && (
          <Line
            points={[mark.anchorX - mark.x, mark.anchorY - mark.y, 0, 0]}
            stroke="#7a6b5f"
            strokeWidth={1}
            dash={[3, 3]}
            lineCap="round"
          />
        )}
        <Line points={[0, 0, 0, -24]} stroke="#263238" strokeWidth={3} lineCap="round" />
        <Rect x={-8} y={-38} width={16} height={18} fill="#263238" stroke="#111" strokeWidth={1} cornerRadius={3} />
        <Circle x={0} y={-29} radius={4} fill="#4caf50" stroke="#dff5df" strokeWidth={1} />
        <Text x={8} y={-43} text={device.name || device.id} fontSize={10} fill="#263238" />
      </Group>
    );
  });

  /**
   * 列車を描画：速度に応じて色を変更（走行中=青、停車中=赤）
   * 列車は線路上（x, y 座標）に描画される
   */
  const trains = (trainStates || []).map((ts) => {
    const speedValue = Number.isFinite(ts.speed) ? ts.speed : 0;
    const isStopped = speedValue <= 0;
    const bodyColor = ts.status === 'WAITING' ? '#f28e2b' : isStopped ? '#e41a1c' : '#2b8cbe';
    const stopRemaining = Number.isFinite(ts.stop_remaining) ? ts.stop_remaining : 0;
    const showStopCountdown = isStopped && stopRemaining > 0;
    const waitLabel = ts.wait_reason ? `${ts.wait_reason}${ts.block_id ? ` ${ts.block_id}` : ''}` : '';
    return (
    <React.Fragment key={ts.train_id}>
      <Group x={ts.x} y={ts.y}>
        <Rect x={-12} y={-6} width={24} height={12} fill={bodyColor} stroke="#17374c" strokeWidth={1} cornerRadius={3} />
        <Rect x={-6} y={-5} width={12} height={5} fill="#f7f7f7" stroke="#17374c" strokeWidth={0.5} />
        <Circle x={-7} y={7} radius={2} fill="#222" />
        <Circle x={7} y={7} radius={2} fill="#222" />
      </Group>
      {showStopCountdown && (
        <Text
          x={ts.x + 8}
          y={ts.y - 24}
          text={`stop ${stopRemaining.toFixed(1)}s`}
          fontSize={11}
          fill="#d62728"
        />
      )}
      {waitLabel && (
        <Text
          x={ts.x + 8}
          y={ts.y - 24}
          text={waitLabel}
          fontSize={11}
          fill="#b35b00"
        />
      )}
      <Text
        x={ts.x + 8}
        y={ts.y - 8}
        text={`${ts.train_id} ${speedValue.toFixed(1)} km/h`}
        fontSize={11}
        fill="#333"
      />
    </React.Fragment>
    );
  });

  return (
    <Stage
      ref={stageRef}
      width={width}
      height={height}
      scaleX={stageScale}
      scaleY={stageScale}
      x={stagePos.x}
      y={stagePos.y}
      onWheel={handleWheel}
      onMouseDown={handleMouseDown}
      onMouseUp={handleMouseUp}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseUp}
    >
      <Layer>
        {/* 線路 → 駅 → 列車の順で描画 */}
        {lines}
        {interlockingMarks}
        {stationNodes}
        {trains}
      </Layer>
    </Stage>
  );
}
