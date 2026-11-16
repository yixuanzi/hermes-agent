// workagent/frontend/src/lib/agent-rpc.ts
type PendingEntry = { resolve: (value: any) => void; reject: (reason: any) => void };

export class AgentRpc {
  private ws: WebSocket | null = null;
  private nextId = 1;
  private pending = new Map<string, PendingEntry>();
  private eventHandlers = new Map<string, Set<(params: any) => void>>();

  /** @internal test hook — override to inject mock WebSocket */
  _createWs(url: string): WebSocket {
    return new WebSocket(url);
  }

  connect(url: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const ws = this._createWs(url);
      this.ws = ws;

      ws.onopen = () => {};
      ws.onmessage = (ev) => {
        let obj: any;
        try {
          obj = JSON.parse(typeof ev.data === "string" ? ev.data : "");
        } catch {
          return;
        }

        // gateway.ready resolves the connect promise
        if (obj.method === "event" && obj.params?.type === "gateway.ready") {
          resolve();
          return;
        }

        // JSON-RPC response
        if (obj.id != null) {
          const entry = this.pending.get(String(obj.id));
          if (entry) {
            this.pending.delete(String(obj.id));
            if (obj.error) {
              entry.reject(obj.error);
            } else {
              entry.resolve(obj.result);
            }
          }
        }

        // Event dispatch
        if (obj.method === "event" && obj.params?.type) {
          const handlers = this.eventHandlers.get(obj.params.type);
          if (handlers) {
            for (const h of handlers) {
              h(obj.params);
            }
          }
        }
      };

      ws.onerror = () => reject(new Error("WebSocket connection failed"));
      ws.onclose = () => {
        // Reject all pending calls
        for (const [, entry] of this.pending) {
          entry.reject(new Error("WebSocket closed"));
        }
        this.pending.clear();
      };
    });
  }

  disconnect(): void {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    for (const [, entry] of this.pending) {
      entry.reject(new Error("WebSocket closed"));
    }
    this.pending.clear();
  }

  call(method: string, params: object = {}): Promise<any> {
    return new Promise((resolve, reject) => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
        reject(new Error("WebSocket not connected"));
        return;
      }
      const id = String(this.nextId++);
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ jsonrpc: "2.0", id, method, params }));
    });
  }

  on(event: string, handler: (params: any) => void): () => void {
    let set = this.eventHandlers.get(event);
    if (!set) {
      set = new Set();
      this.eventHandlers.set(event, set);
    }
    set.add(handler);
    return () => {
      set!.delete(handler);
      if (set!.size === 0) this.eventHandlers.delete(event);
    };
  }
}
