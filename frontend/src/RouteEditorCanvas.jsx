import React from 'react';
import { Stage, Layer, Circle, Line, Text, Group, Rect } from 'react-konva';

/**
 * ルートエディタ - Konva キャンバス描画のみ
 * 操作パネルは App.jsx で管理
 */
export default function RouteEditorCanvas({
  network,
  setNetwork,
  selectedStation,
  setSelectedStation,
  connectionMode,
  setConnectionMode,
  firstStationForConnection,
  setFirstStationForConnection,
  routeEditing = false,
  routeDraftStationIds = [],
  onRouteStationClick,
  selectedInterlockingDevice = null,
  onInterlockingDeviceClick,
  width = 900,
  height = 500
}) {
  // ステーション管理
  const stations = network?.stations || [];
  // セグメント（線路）管理
  const segments = network?.segments || [];
  const exclusiveSegmentIds = new Set(
    (network?.exclusive_sections || []).flatMap((section) => section.segment_ids || [])
  );
  const interlockingDevices = network?.interlocking_devices || [];
  const stationById = new Map(stations.map((station) => [station.id, station]));
  const segmentById = new Map(segments.map((segment) => [segment.id, segment]));
  const sectionById = new Map((network?.exclusive_sections || []).map((section) => [section.id, section]));
  const routeOrderByStationId = new Map();
  routeDraftStationIds.forEach((stationId, index) => {
    if (!routeOrderByStationId.has(stationId)) {
      routeOrderByStationId.set(stationId, index + 1);
    }
  });

  const stageScaleRef = React.useRef(1);
  const stagePosRef = React.useRef({ x: 0, y: 0 });
  const isPanningRef = React.useRef(false);
  const lastPointerRef = React.useRef(null);
  const [stageScale, setStageScale] = React.useState(1);
  const [stagePos, setStagePos] = React.useState({ x: 0, y: 0 });

  const setStageCursor = (target, cursor) => {
    if (isPanningRef.current) return;
    const stage = target?.getStage?.();
    const container = stage?.container?.();
    if (container) {
      container.style.cursor = cursor;
    }
  };

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

  // ネットワークが変更されたら localStorage に保存
  React.useEffect(() => {
    if (network && network.stations && network.segments) {
      localStorage.setItem('route_network_v2', JSON.stringify(network));
    }
  }, [network]);

  const stationRadius = 12;

  const getSegmentDistance = (segment) => {
    if (Number.isFinite(segment?.length) && segment.length > 0) return segment.length;
    const start = stationById.get(segment?.start);
    const end = stationById.get(segment?.end);
    if (!start || !end) return 0;
    return Math.hypot(end.x - start.x, end.y - start.y);
  };

  const pointOnRoute = (route, targetDistance) => {
    let accumulated = 0;
    for (const leg of route?.legs || []) {
      const segment = segmentById.get(leg.segment_id);
      const startStation = stationById.get(leg.from);
      const endStation = stationById.get(leg.to);
      if (!segment || !startStation || !endStation) continue;
      const length = getSegmentDistance(segment);
      if (targetDistance <= accumulated + length || leg === route.legs[route.legs.length - 1]) {
        const ratio = length > 0 ? Math.max(0, Math.min(1, (targetDistance - accumulated) / length)) : 0;
        const dx = endStation.x - startStation.x;
        const dy = endStation.y - startStation.y;
        const visualLength = Math.hypot(dx, dy);
        return {
          x: startStation.x + (endStation.x - startStation.x) * ratio,
          y: startStation.y + (endStation.y - startStation.y) * ratio,
          nx: visualLength > 0 ? -dy / visualLength : 0,
          ny: visualLength > 0 ? dx / visualLength : -1
        };
      }
      accumulated += length;
    }
    return null;
  };

  const interlockingDevicePoint = (device) => {
    const route = (network?.routes || []).find((r) => r.id === device.route_id);
    const section = sectionById.get(device.section_id);
    if (!route || !section) return null;
    let accumulated = 0;
    for (const leg of route.legs || []) {
      if ((section.segment_ids || []).includes(leg.segment_id)) {
        const target = Math.max(0, accumulated - (Number(device.approach_distance) || 0));
        return pointOnRoute(route, target);
      }
      accumulated += getSegmentDistance(segmentById.get(leg.segment_id));
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
   * 駅をドラッグで移動
   */
  const handleStationDrag = (stationId, newX, newY) => {
    const updatedStations = stations.map(s =>
      s.id === stationId ? { ...s, x: newX, y: newY } : s
    );
    setNetwork({ ...network, stations: updatedStations });
  };

  /**
   * 2つの駅を線路で繋ぐ（接続モード）
   */
  const startConnection = (stationId) => {
    if (connectionMode && firstStationForConnection === stationId) {
      setConnectionMode(false);
      setFirstStationForConnection(null);
    } else if (connectionMode) {
      const segmentAlreadyExists = segments.some(
        seg => (seg.start === firstStationForConnection && seg.end === stationId) ||
               (seg.start === stationId && seg.end === firstStationForConnection)
      );
      if (!segmentAlreadyExists) {
        const newSegmentId = `E${Math.max(0, ...segments.map(s => parseInt(s.id.substring(1)) || 0)) + 1}`;
        const updatedSegments = [
          ...segments,
          { id: newSegmentId, start: firstStationForConnection, end: stationId }
        ];
        setNetwork({ ...network, segments: updatedSegments, routes: network.routes || [], exclusive_sections: network.exclusive_sections || [] });
      }
      setConnectionMode(false);
      setFirstStationForConnection(null);
    } else {
      setConnectionMode(true);
      setFirstStationForConnection(stationId);
    }
  };

  /**
   * 線路を削除
   */
  const deleteSegment = (segmentId) => {
    const updatedSegments = segments.filter(s => s.id !== segmentId);
    const updatedRoutes = (network.routes || [])
      .filter((route) => !(route.legs || []).some((leg) => leg.segment_id === segmentId));
    const validRouteIds = new Set(updatedRoutes.map((route) => route.id));
    const updatedSections = (network.exclusive_sections || [])
      .map((section) => ({ ...section, segment_ids: (section.segment_ids || []).filter((id) => id !== segmentId) }))
      .filter((section) => section.segment_ids.length > 0)
      .map((section) => ({
        ...section,
        priority_route_ids: (section.priority_route_ids || []).filter((id) => validRouteIds.has(id))
      }));
    const validSectionIds = new Set(updatedSections.map((section) => section.id));
    const updatedInterlocks = interlockingDevices.filter((device) => (
      validRouteIds.has(device.route_id) && validSectionIds.has(device.section_id)
    ));
    setNetwork({ ...network, segments: updatedSegments, routes: updatedRoutes, exclusive_sections: updatedSections, interlocking_devices: updatedInterlocks });
  };

  // キャンバスサイズ（ウィンドウサイズに応じて）
  const canvasWidth = width;
  const canvasHeight = height;

  const addNodeAtCanvasCenter = (kind) => {
    const nodeKind = kind === 'waypoint' ? 'waypoint' : 'station';
    const nextStationId = `S${Math.max(0, ...stations.map((s) => parseInt(String(s.id).replace(/\D/g, ''), 10) || 0)) + 1}`;
    const centerX = (canvasWidth / 2 - stagePosRef.current.x) / stageScaleRef.current;
    const centerY = (canvasHeight / 2 - stagePosRef.current.y) / stageScaleRef.current;
    const defaultName = nodeKind === 'waypoint' ? `通過点 ${nextStationId}` : `駅 ${nextStationId}`;
    const nextStation = {
      id: nextStationId,
      name: defaultName,
      kind: nodeKind,
      x: Math.round(centerX * 10) / 10,
      y: Math.round(centerY * 10) / 10,
      length: nodeKind === 'station' ? 200 : 0,
      stop_time: nodeKind === 'station' ? 30 : 0
    };
    setNetwork({
      ...network,
      stations: [...stations, nextStation],
      routes: network.routes || [],
      exclusive_sections: network.exclusive_sections || []
    });
    setSelectedStation(nextStationId);
    setConnectionMode(false);
    setFirstStationForConnection(null);
  };

  /**
   * Konva Circle コンポーネント（ドラッグ可能な駅）
   */
  const StationNode = ({ station, isSelected, isConnectionSource, routeOrder }) => {
    const draggableRef = React.useRef();
    const isWaypoint = station.kind === 'waypoint';

    const handleDragEnd = (e) => {
      if (routeEditing) return;
      handleStationDrag(station.id, e.target.x(), e.target.y());
    };

    const handleStationClick = (e) => {
      e.cancelBubble = true;
      if (routeEditing) {
        onRouteStationClick?.(station.id);
        return;
      }
      setSelectedStation(station.id);
      startConnection(station.id);
    };

    return (
      <React.Fragment key={station.id}>
        <Group
          ref={draggableRef}
          x={station.x}
          y={station.y}
          draggable={!routeEditing}
          onDragEnd={handleDragEnd}
          onClick={handleStationClick}
          onTap={handleStationClick}
          onMouseEnter={(e) => setStageCursor(e.target, routeEditing ? 'pointer' : 'grab')}
          onMouseLeave={(e) => setStageCursor(e.target, 'default')}
        >
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
          {routeOrder && <Circle x={0} y={-28} radius={10} fill="#1f6feb" stroke="#0b3d91" strokeWidth={1} />}
          {routeOrder && <Text x={-10} y={-34} width={20} text={String(routeOrder)} fontSize={11} fill="#fff" align="center" />}
          {isSelected && <Circle x={0} y={10} radius={4} fill="#ff6b6b" stroke="#cc0000" strokeWidth={1} />}
          {isConnectionSource && <Circle x={0} y={10} radius={4} fill="#ffd93d" stroke="#a17f00" strokeWidth={1} />}
        </Group>
        <Text
          x={station.x - 24}
          y={station.y + stationRadius + 10}
          text={station.name}
          fontSize={11}
          width={48}
          align="center"
        />
      </React.Fragment>
    );
  };

  /**
   * 線路（セグメント）を描画
   */
  const SegmentLines = () => {
    return segments.map((segment) => {
      const startStation = stations.find(s => s.id === segment.start);
      const endStation = stations.find(s => s.id === segment.end);

      if (!startStation || !endStation) return null;

      return (
        <React.Fragment key={segment.id}>
          {/* 線路 */}
          <Line
            points={[startStation.x, startStation.y, endStation.x, endStation.y]}
            stroke={exclusiveSegmentIds.has(segment.id) ? '#b33a3a' : '#444'}
            strokeWidth={exclusiveSegmentIds.has(segment.id) ? 8 : 6}
            lineCap="round"
            onClick={() => {
              if (!routeEditing) deleteSegment(segment.id);
            }}
            onMouseEnter={(e) => setStageCursor(e.target, routeEditing ? 'default' : 'pointer')}
            onMouseLeave={(e) => setStageCursor(e.target, 'default')}
          />
          <Line
            points={[startStation.x, startStation.y, endStation.x, endStation.y]}
            stroke="#888"
            strokeWidth={2}
            dash={[6, 6]}
            lineCap="round"
            onClick={() => {
              if (!routeEditing) deleteSegment(segment.id);
            }}
            onMouseEnter={(e) => setStageCursor(e.target, routeEditing ? 'default' : 'pointer')}
            onMouseLeave={(e) => setStageCursor(e.target, 'default')}
          />
          {/* 線路のラベル（ID） */}
          <Text
            x={(startStation.x + endStation.x) / 2 - 10}
            y={(startStation.y + endStation.y) / 2 - 10}
            text={segment.id}
            fontSize={9}
            fill="#999"
          />
        </React.Fragment>
      );
    });
  };

  const RoutePreviewLines = () => {
    if (!routeEditing || routeDraftStationIds.length < 2) return null;
    const segmentExists = (fromId, toId) => segments.some((segment) => (
      (segment.start === fromId && segment.end === toId) ||
      (segment.start === toId && segment.end === fromId)
    ));
    return routeDraftStationIds.slice(1).map((toId, index) => {
      const fromId = routeDraftStationIds[index];
      const startStation = stationById.get(fromId);
      const endStation = stationById.get(toId);
      if (!startStation || !endStation) return null;
      const hasSegment = segmentExists(fromId, toId);
      const midX = (startStation.x + endStation.x) / 2;
      const midY = (startStation.y + endStation.y) / 2;
      return (
        <React.Fragment key={`${fromId}-${toId}-${index}`}>
          <Line
            points={[startStation.x, startStation.y, endStation.x, endStation.y]}
            stroke={hasSegment ? '#1f6feb' : '#d64545'}
            strokeWidth={4}
            dash={hasSegment ? [10, 6] : [4, 4]}
            lineCap="round"
          />
          {!hasSegment && (
            <Text
              x={midX - 28}
              y={midY - 18}
              width={56}
              text="線路なし"
              fontSize={10}
              fill="#d64545"
              align="center"
            />
          )}
        </React.Fragment>
      );
    });
  };

  const InterlockingMarks = () => (
    layoutInterlockingDevices().map((mark) => {
      const { device } = mark;
      const isSelected = selectedInterlockingDevice === device.id;
      const handleClick = (e) => {
        e.cancelBubble = true;
        onInterlockingDeviceClick?.(device.id);
      };
      return (
        <Group
          key={device.id}
          x={mark.x}
          y={mark.y}
          onClick={handleClick}
          onTap={handleClick}
          onMouseEnter={(e) => setStageCursor(e.target, 'pointer')}
          onMouseLeave={(e) => setStageCursor(e.target, 'default')}
        >
          {mark.offsetDistance > 0 && (
            <Line
              points={[mark.anchorX - mark.x, mark.anchorY - mark.y, 0, 0]}
              stroke="#7a6b5f"
              strokeWidth={1}
              dash={[3, 3]}
              lineCap="round"
            />
          )}
          <Line points={[0, 0, 0, -24]} stroke={isSelected ? '#c06b00' : '#263238'} strokeWidth={isSelected ? 4 : 3} lineCap="round" />
          {isSelected && <Circle x={0} y={-29} radius={13} fill="rgba(255, 193, 7, 0.22)" stroke="#f59f00" strokeWidth={2} />}
          <Rect x={-8} y={-38} width={16} height={18} fill={isSelected ? '#f59f00' : '#263238'} stroke={isSelected ? '#9a5b00' : '#111'} strokeWidth={1} cornerRadius={3} />
          <Circle x={0} y={-29} radius={4} fill={isSelected ? '#fff3bf' : '#4caf50'} stroke="#dff5df" strokeWidth={1} />
          <Text x={8} y={-43} text={device.name || device.id} fontSize={10} fill={isSelected ? '#9a5b00' : '#263238'} />
        </Group>
      );
    })
  );

  return (
    <div style={{ position: 'relative', width: canvasWidth, height: canvasHeight }}>
      <div
        style={{
          position: 'absolute',
          top: 10,
          left: 10,
          zIndex: 10,
          display: 'flex',
          flexDirection: 'column',
          gap: 6,
          padding: 6,
          background: 'rgba(255, 255, 255, 0.94)',
          border: '1px solid #d65f45',
          borderRadius: 8,
          boxShadow: '0 1px 4px rgba(0,0,0,0.15)'
        }}
      >
        <button type="button" onClick={() => addNodeAtCanvasCenter('station')} disabled={routeEditing} title="駅をキャンバス中心に追加">
          駅
        </button>
        <button type="button" onClick={() => addNodeAtCanvasCenter('waypoint')} disabled={routeEditing} title="通過点をキャンバス中心に追加">
          通過点
        </button>
      </div>
      <Stage
        width={canvasWidth}
        height={canvasHeight}
        style={{ backgroundColor: '#f9f9f9' }}
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
          {/* 線路を描画 */}
          <SegmentLines />
          <RoutePreviewLines />
          <InterlockingMarks />
          {/* 駅を描画 */}
          {stations.map((station) => (
            <StationNode
              key={station.id}
              station={station}
              isSelected={selectedStation === station.id}
              isConnectionSource={connectionMode && firstStationForConnection === station.id}
              routeOrder={routeOrderByStationId.get(station.id)}
            />
          ))}
        </Layer>
      </Stage>
    </div>
  );
}
