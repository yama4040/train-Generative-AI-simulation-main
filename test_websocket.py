#!/usr/bin/env python3
"""
WebSocket シミュレーション通信テスト
バックエンドの動作確認用スクリプト
"""
import asyncio
import json
import websockets
import sys
import os


async def test_websocket_simulation():
    """
    WebSocket でシミュレーションを実行し、結果を確認
    """
    port = os.getenv("WEBSOCKET_PORT", "8000")
    uri = f"ws://127.0.0.1:{port}/ws/sim"
    
    # テスト用の列車設定
    test_payload = {
        "trains": [
            {
                "train_id": "T1",
                "route": ["S1", "S2", "S3"],
                "max_speed": 50,
                "accel": 5.0,
                "decel": 5.0
            }
        ],
        "dt": 0.5,
        "duration": 10.0,
        "simulation_mode": "low_precision"
    }
    
    try:
        print("[テスト] WebSocket 接続を開始...")
        async with websockets.connect(uri) as websocket:
            print("[テスト] WebSocket 接続成功")
            
            # ペイロードを送信
            print("[テスト] シミュレーション パラメータを送信...")
            await websocket.send(json.dumps(test_payload))
            
            # 結果を受信
            state_count = 0
            print("[テスト] シミュレーション結果の受信を開始...")
            async for message in websocket:
                state = json.loads(message)
                state_count += 1
                if state_count <= 3 or state_count % 5 == 0:
                    print(f"  [受信 #{state_count}] train_id={state.get('train_id')}, time={state.get('time')}, x={state.get('x'):.1f}, y={state.get('y'):.1f}, speed={state.get('speed'):.1f}")
            
            print(f"[テスト] ✓ 合計 {state_count} 件の状態を受信しました")
            return True
            
    except Exception as e:
        print(f"[テスト] ✗ エラーが発生しました: {e}")
        return False


if __name__ == "__main__":
    result = asyncio.run(test_websocket_simulation())
    sys.exit(0 if result else 1)
