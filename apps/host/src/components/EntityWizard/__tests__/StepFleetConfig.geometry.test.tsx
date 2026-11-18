/* eslint-disable @typescript-eslint/no-explicit-any */
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';

vi.mock('@/context/I18nContext', () => ({ useI18n: () => ({ t: (k: string) => k }) }));

let latest: any = null;
vi.mock('../WizardContext', () => ({ useWizard: () => latest.ctx }));

import { StepFleetConfig } from '../steps/StepFleetConfig';

function Harness({ type = 'ManufacturingMachine' }: { type?: string }) {
  const [formData, setFormData] = useState<any>({
    macroCategory: 'fleet', name: 'M', description: '', geometry: null, geometryType: 'Point', additionalAttributes: {},
  });
  latest = { ctx: { entityType: type, formData, updateFormData: (p: any) => setFormData((f: any) => ({ ...f, ...p })) } };
  return <StepFleetConfig />;
}

const input = (labelKey: string) =>
  screen.getByText(labelKey).parentElement!.querySelector('input, select') as HTMLInputElement;

describe('StepFleetConfig machine geometry', () => {
  it('asks the role first, tractor by default, with the tractor geometry', () => {
    render(<Harness />);
    expect((input('editor.field.category') as unknown as HTMLSelectElement).value).toBe('tractor');
    expect(screen.getByText('editor.field.minTurningRadius')).toBeTruthy();
    expect(screen.getByText('editor.help.minTurningRadius')).toBeTruthy();
    expect(screen.queryByText('editor.field.mountPosition')).toBeNull();
  });

  it('switches to the implement geometry and stores the values in metres', () => {
    render(<Harness />);
    fireEvent.change(input('editor.field.category'), { target: { value: 'implement' } });
    expect(screen.getByText('editor.field.mountPosition')).toBeTruthy();
    expect(screen.queryByText('editor.field.minTurningRadius')).toBeNull();
    fireEvent.change(input('editor.field.implementWidth'), { target: { value: '6' } });
    fireEvent.change(input('editor.field.mountPosition'), { target: { value: 'front' } });
    expect(latest.ctx.formData.category).toBe('implement');
    expect(latest.ctx.formData.machineGeometry).toEqual({ implementWidth: 6, mountPosition: 'front' });
  });

  it('a cleared field leaves no value behind', () => {
    render(<Harness />);
    fireEvent.change(input('editor.field.trackWidth'), { target: { value: '1.9' } });
    fireEvent.change(input('editor.field.trackWidth'), { target: { value: '' } });
    expect(latest.ctx.formData.machineGeometry).toEqual({});
  });

  it('robots get no machine geometry section', () => {
    render(<Harness type="AutonomousMobileRobot" />);
    expect(screen.queryByText('editor.field.category')).toBeNull();
  });

  it('the role select has no empty option', () => {
    render(<Harness />);
    const options = Array.from((input('editor.field.category') as unknown as HTMLSelectElement).options).map((o) => o.value);
    expect(options).toEqual(['tractor', 'implement']);
  });
});

