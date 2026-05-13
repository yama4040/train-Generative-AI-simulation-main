import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi, expect, describe, beforeEach, test } from 'vitest';
import TrainEditor from '../TrainEditor';

describe('TrainEditor presets', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  test('saves and loads a preset', async () => {
    const trains = [{ train_id: 'T1', route_id: 'R_A', max_speed: 40, accel: 2, decel: 2, length: 100, start_time: 0 }];
    const setTrains = vi.fn();
    const routes = [{ id: 'R_A', name: 'Route A', legs: [] }];

    render(<TrainEditor trains={trains} setTrains={setTrains} routes={routes} />);

    // enter preset name and save
    const input = screen.getByPlaceholderText('プリセット名');
    fireEvent.change(input, { target: { value: 'demo' } });
    const saveBtn = screen.getByRole('button', { name: 'プリセットを保存' });
    fireEvent.click(saveBtn);

    // preset should appear
    await screen.findByText('demo');

    // click load -> setTrains called with preset trains
    const loadBtn = screen.getByRole('button', { name: '読み込み' });
    fireEvent.click(loadBtn);
    expect(setTrains).toHaveBeenCalled();
    const calledWith = setTrains.mock.calls[0][0];
    expect(calledWith[0].train_id).toBe('T1');
    expect(calledWith[0].route_id).toBe('R_A');
  });

  test('assigns the first route when an existing train has no route_id', async () => {
    const trains = [{ train_id: 'T1', route_id: '', max_speed: 40, accel: 2, decel: 2, length: 100, start_time: 0 }];
    const setTrains = vi.fn();
    const routes = [{ id: 'R_1', name: 'R_1', legs: [] }, { id: 'R_2', name: 'R_2', legs: [] }];

    render(<TrainEditor trains={trains} setTrains={setTrains} routes={routes} />);

    await waitFor(() => {
      expect(setTrains).toHaveBeenCalledWith([
        { ...trains[0], route_id: 'R_1' }
      ]);
    });
  });
});
