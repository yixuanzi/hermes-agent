import { getStoredToken } from "./auth";

const CHAT_SESSION_KEY = "workagent.chat.resumeSessionId";
export const CHAT_SESSION_CHANGED_EVENT = "workagent:chat-session-changed";

export function generateChannelId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `workagent-${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`;
}

function wsBaseUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}`;
}

export function buildPtyUrl({
  channel,
  resume,
}: {
  channel: string;
  resume: string | null;
}): string {
  const qs = new URLSearchParams({
    token: getStoredToken(),
    channel,
  });
  if (resume) qs.set("resume", resume);
  return `${wsBaseUrl()}/api/chat/pty?${qs.toString()}`;
}

export function buildEventsUrl(channel: string): string {
  const qs = new URLSearchParams({
    token: getStoredToken(),
    channel,
  });
  return `${wsBaseUrl()}/api/chat/events?${qs.toString()}`;
}

export function buildGatewayUrl(channel: string): string {
  const qs = new URLSearchParams({
    token: getStoredToken(),
    channel,
  });
  return `${wsBaseUrl()}/api/chat/ws?${qs.toString()}`;
}

export function getStoredChatResumeSession(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(CHAT_SESSION_KEY) || "";
}

export function setStoredChatResumeSession(sessionId: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(CHAT_SESSION_KEY, sessionId);
  window.dispatchEvent(new CustomEvent(CHAT_SESSION_CHANGED_EVENT, { detail: { sessionId } }));
}

export function clearStoredChatResumeSession(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(CHAT_SESSION_KEY);
  window.dispatchEvent(new CustomEvent(CHAT_SESSION_CHANGED_EVENT, { detail: { sessionId: "" } }));
}
