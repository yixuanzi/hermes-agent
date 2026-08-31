import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import LoginScreen from '../LoginScreen';

describe('LoginScreen', () => {
  afterEach(cleanup);

  it('keeps credentials empty and exposes both SSO entries when Lark SSO is enabled', () => {
    const onAegisSsoLogin = vi.fn();
    const onLarkSsoLogin = vi.fn();
    render(
      <LoginScreen
        onSubmit={vi.fn()}
        onAegisSsoLogin={onAegisSsoLogin}
        onLarkSsoLogin={onLarkSsoLogin}
        larkSsoEnabled
        onSwitchToRegister={vi.fn()}
        pending={false}
      />,
    );

    expect(screen.getByLabelText('Username')).toHaveValue('');
    expect(screen.getByLabelText('Password')).toHaveValue('');
    fireEvent.click(screen.getByRole('button', { name: 'Aegis SSO' }));
    expect(onAegisSsoLogin).toHaveBeenCalledOnce();
    expect(onLarkSsoLogin).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Lark SSO' }));
    expect(onLarkSsoLogin).toHaveBeenCalledOnce();
  });

  it('hides the Lark SSO entry when it is disabled', () => {
    render(
      <LoginScreen
        onSubmit={vi.fn()}
        onAegisSsoLogin={vi.fn()}
        onLarkSsoLogin={vi.fn()}
        larkSsoEnabled={false}
        onSwitchToRegister={vi.fn()}
        pending={false}
      />,
    );

    expect(screen.queryByRole('button', { name: 'Lark SSO' })).not.toBeInTheDocument();
  });
});
