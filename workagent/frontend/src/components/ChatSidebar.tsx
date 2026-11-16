type ChatSidebarProps = {
  events: string[];
};

export function ChatSidebar({ events }: ChatSidebarProps) {
  return (
    <aside className="chat-sidebar">
      <h3>Event Feed</h3>
      <p className="subtle-copy">Realtime gateway events from `/api/chat/events`.</p>
      <div className="chat-events">
        {events.length === 0 ? <p>No events yet.</p> : null}
        {events.map((line, index) => (
          <pre key={`${index}-${line.slice(0, 16)}`}>{line}</pre>
        ))}
      </div>
    </aside>
  );
}

