import { FormEvent, useEffect, useState } from "react";
import { Navigate } from "react-router-dom";

import { PageMissionHeader } from "../components/PageMissionHeader";
import { fetchJSON } from "../lib/api";
import { useCurrentUser } from "../lib/authContext";
import type { AuthenticatedUser, UserStatus } from "../types";

interface UserListResponse {
  users: AuthenticatedUser[];
}

export function UsersPage() {
  const currentUser = useCurrentUser();
  const [users, setUsers] = useState<AuthenticatedUser[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const [newUsername, setNewUsername] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [creating, setCreating] = useState(false);

  const [resetPasswords, setResetPasswords] = useState<Record<string, string>>({});

  async function loadUsers(): Promise<void> {
    try {
      const payload = await fetchJSON<UserListResponse>("/api/users");
      setUsers(payload.users);
      setError("");
    } catch {
      setError("Failed to load users.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadUsers();
  }, []);

  async function handleCreate(event: FormEvent): Promise<void> {
    event.preventDefault();
    setCreating(true);
    setError("");
    try {
      await fetchJSON("/api/users", {
        method: "POST",
        body: JSON.stringify({
          username: newUsername,
          password: newPassword,
          email: newEmail,
          status: "enabled",
        }),
      });
      setNewUsername("");
      setNewPassword("");
      setNewEmail("");
      await loadUsers();
    } catch {
      setError("Failed to create user. Check the username, password, and email.");
    } finally {
      setCreating(false);
    }
  }

  async function toggleStatus(uid: string, status: UserStatus): Promise<void> {
    setError("");
    try {
      await fetchJSON(`/api/users/${uid}/status`, {
        method: "PUT",
        body: JSON.stringify({ status }),
      });
      await loadUsers();
    } catch {
      setError("Failed to update user status.");
    }
  }

  async function resetPassword(uid: string): Promise<void> {
    const password = resetPasswords[uid] || "";
    if (password.length < 8) {
      setError("New password must be at least 8 characters.");
      return;
    }
    setError("");
    try {
      await fetchJSON(`/api/users/${uid}/password`, {
        method: "PUT",
        body: JSON.stringify({ password }),
      });
      setResetPasswords((prev) => ({ ...prev, [uid]: "" }));
    } catch {
      setError("Failed to reset password.");
    }
  }

  async function deleteUser(uid: string): Promise<void> {
    if (!window.confirm("Delete this user? This cannot be undone.")) return;
    setError("");
    try {
      await fetchJSON(`/api/users/${uid}`, { method: "DELETE" });
      await loadUsers();
    } catch {
      setError("Failed to delete user.");
    }
  }

  if (currentUser && !currentUser.is_admin) {
    return <Navigate to="/overview" replace />;
  }

  return (
    <section className="users-workbench-page">
      <PageMissionHeader title="Users" subtitle="Manage AISOC accounts, access, and passwords." />

      {error ? <p className="error-text">{error}</p> : null}

      <article className="detail-panel">
        <h3>Create User</h3>
        <form className="grid gap-[calc(10px*var(--density-scale))]" onSubmit={handleCreate}>
          <label htmlFor="new-username">Username</label>
          <input
            id="new-username"
            type="text"
            value={newUsername}
            onChange={(event) => setNewUsername(event.target.value)}
            required
          />
          <label htmlFor="new-password">Password</label>
          <input
            id="new-password"
            type="password"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            required
          />
          <label htmlFor="new-email">Email</label>
          <input
            id="new-email"
            type="email"
            value={newEmail}
            onChange={(event) => setNewEmail(event.target.value)}
            required
          />
          <button type="submit" disabled={creating}>
            {creating ? "Creating..." : "Create User"}
          </button>
        </form>
      </article>

      <article className="detail-panel">
        <h3>All Users</h3>
        {loading ? <p className="subtle-copy">Loading users…</p> : null}
        {!loading && users.length === 0 ? <p className="subtle-copy">No users found.</p> : null}
        {users.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>Username</th>
                <th>Email</th>
                <th>Status</th>
                <th>Admin</th>
                <th>Last Login</th>
                <th>Reset Password</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr key={user.uid}>
                  <td>{user.username}</td>
                  <td>{user.email}</td>
                  <td>
                    <span className={`status-badge ${user.status === "enabled" ? "status-live" : ""}`}>
                      {user.status}
                    </span>
                  </td>
                  <td>{user.is_admin ? "Yes" : "No"}</td>
                  <td>{user.last_login || "Never"}</td>
                  <td>
                    <input
                      type="password"
                      placeholder="New password"
                      value={resetPasswords[user.uid] || ""}
                      onChange={(event) =>
                        setResetPasswords((prev) => ({ ...prev, [user.uid]: event.target.value }))
                      }
                    />
                    <button type="button" onClick={() => void resetPassword(user.uid)}>
                      Reset
                    </button>
                  </td>
                  <td>
                    {!user.is_admin ? (
                      <>
                        <button
                          type="button"
                          onClick={() =>
                            void toggleStatus(user.uid, user.status === "enabled" ? "disabled" : "enabled")
                          }
                        >
                          {user.status === "enabled" ? "Disable" : "Enable"}
                        </button>
                        <button type="button" className="danger-button" onClick={() => void deleteUser(user.uid)}>
                          Delete
                        </button>
                      </>
                    ) : (
                      <span className="subtle-copy">Protected</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : null}
      </article>
    </section>
  );
}
