import { FormEvent, useEffect, useState } from 'react';

interface ChangePasswordDialogProps {
  open: boolean;
  pending: boolean;
  onClose: () => void;
  onSubmit: (oldPassword: string, newPassword: string) => Promise<void>;
}

export default function ChangePasswordDialog({
  open,
  pending,
  onClose,
  onSubmit,
}: ChangePasswordDialogProps) {
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [localError, setLocalError] = useState('');

  useEffect(() => {
    if (!open) {
      setOldPassword('');
      setNewPassword('');
      setLocalError('');
    }
  }, [open]);

  if (!open) {
    return null;
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!oldPassword.trim() || !newPassword.trim()) {
      setLocalError('请输入旧密码和新密码。');
      return;
    }
    setLocalError('');
    await onSubmit(oldPassword, newPassword);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4">
      <div className="w-full max-w-md rounded-2xl border border-slate-800 bg-[#05080F] p-6 shadow-2xl">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold text-white">Change Password</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-slate-700 px-3 py-1 text-xs font-semibold text-slate-300 transition hover:border-cyan-500 hover:text-white"
          >
            Close
          </button>
        </div>

        <form className="mt-6 space-y-4" onSubmit={handleSubmit}>
          <label className="block space-y-2">
            <span className="text-[10px] font-mono uppercase tracking-[0.24em] text-slate-500">
              Current Password
            </span>
            <input
              aria-label="Current Password"
              type="password"
              autoComplete="current-password"
              value={oldPassword}
              onChange={(event) => setOldPassword(event.target.value)}
              className="w-full rounded-xl border border-slate-800 bg-[#020408] px-4 py-3 text-sm text-white outline-none transition focus:border-cyan-500"
            />
          </label>
          <label className="block space-y-2">
            <span className="text-[10px] font-mono uppercase tracking-[0.24em] text-slate-500">
              New Password
            </span>
            <input
              aria-label="New Password"
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              className="w-full rounded-xl border border-slate-800 bg-[#020408] px-4 py-3 text-sm text-white outline-none transition focus:border-cyan-500"
            />
          </label>
          <button
            type="submit"
            disabled={pending}
            className="w-full rounded-xl bg-cyan-500 px-4 py-3 text-sm font-bold text-white transition hover:bg-cyan-600 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {pending ? 'Updating...' : 'Update Password'}
          </button>
          {localError ? <p className="text-sm text-rose-400">{localError}</p> : null}
        </form>
      </div>
    </div>
  );
}
