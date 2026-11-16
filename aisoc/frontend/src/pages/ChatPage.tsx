/**
 * 统一聊天页（薄组装层）：读取 useChatRuntime，组合
 * SessionListPane / MessageStream / Composer / ChatDrawer；
 * 处理 pendingHtmlPreviews 自动打开 drawer 文件 tab；支持 ?session=<id>；
 * 支持 ?quick=<name>（进入后向 composer 插入 `@[<name>] ` 草稿并清掉参数）。
 */
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import {
  ChatDrawer,
  ChatLayout,
  Composer,
  MessageStream,
  RunStateIndicator,
  SessionListPane,
} from "../chat/components";
import {
  CachedDrawerTabsByConversation,
  CachedDynamicDrawerTab,
  CachedWorkflowDrawerTab,
  loadCachedDrawerTabs,
  saveCachedDrawerTabs,
} from "../chat/lib/chatDrawerTabs";
import { dispatchComposerInsert } from "../chat/lib/composerBus";
import { useChatRuntime } from "../chat/runtime/chatRuntime";

/** drawer tab 持久化的存储命名空间（后端无用户体系时的固定值）。 */
const DRAWER_TABS_USER_ID = "aisoc-web";

function drawerTabId(path: string): CachedDynamicDrawerTab["id"] {
  return `file:${encodeURIComponent(path)}`;
}

export function ChatPage() {
  const {
    activeConvId,
    activeConversation,
    conversations,
    pendingHtmlPreviews,
    consumePendingHtmlPreview,
    openSession,
  } = useChatRuntime();
  const [searchParams, setSearchParams] = useSearchParams();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerFullscreen, setDrawerFullscreen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [drawerTabsByConversation, setDrawerTabsByConversation] =
    useState<CachedDrawerTabsByConversation>({});
  const [refreshNonceByTab, setRefreshNonceByTab] = useState<Record<string, number>>({});
  // drawer tab 缓存必须用能在刷新前后保持不变的 key。新会话刚创建时的
  // conversation.id 是本 tab 生成的本地 id（createLocalId()），session.bound
  // 之后才会拿到后端 sessionId，但 id 字段本身在这个 tab 的生命周期里不会变；
  // 刷新后 conversations 完全由 /api/sessions 重建，此时
  // conversationFromSessionItem() 把 id 直接设成了 sessionId —— 同一个会话在
  // 刷新前后的 id 是两个不同的字符串。所以缓存 key 一律优先用 sessionId，只有
  // 还没绑定成功（没有 sessionId）时才退回本地 id。
  const drawerCacheKey = (conversation?: { id: string; sessionId?: string }) =>
    conversation?.sessionId || conversation?.id || "";
  const activeDrawerKey = drawerCacheKey(activeConversation) || activeConvId;
  const restoredConvIdsRef = useRef<Set<string>>(new Set());
  const autoOpenAttemptedRef = useRef<Set<string>>(new Set());
  const skipNextDrawerSaveRef = useRef(false);
  const handledSessionParamRef = useRef("");
  const handledQuickParamRef = useRef(false);

  // ?session=<id> → 打开（或创建占位）该会话。
  const sessionParam = (searchParams.get("session") || "").trim();
  useEffect(() => {
    if (!sessionParam || handledSessionParamRef.current === sessionParam) {
      return;
    }
    handledSessionParamRef.current = sessionParam;
    openSession(sessionParam);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionParam]);

  // ?quick=<name> → composer 挂载后插入 `@[<name>] ` 草稿，然后清掉参数。
  const quickParam = (searchParams.get("quick") || "").trim();
  useEffect(() => {
    if (!quickParam || handledQuickParamRef.current) {
      return;
    }
    handledQuickParamRef.current = true;
    // setTimeout 0：等 Composer 完成挂载并订阅 composerBus 后再派发插入事件。
    const timer = setTimeout(() => {
      dispatchComposerInsert(`@[${quickParam}] `);
    }, 0);
    const next = new URLSearchParams(searchParams);
    next.delete("quick");
    setSearchParams(next, { replace: true });
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [quickParam]);

  // 会话列表就绪后按会话恢复持久化的 drawer 文件 tab。
  useEffect(() => {
    const ids = conversations.map((conversation) => drawerCacheKey(conversation));
    const newIds = ids.filter((id) => !restoredConvIdsRef.current.has(id));
    if (newIds.length === 0) {
      return;
    }
    newIds.forEach((id) => restoredConvIdsRef.current.add(id));
    const restored = loadCachedDrawerTabs(DRAWER_TABS_USER_ID, newIds);
    if (Object.keys(restored).length > 0) {
      // setDrawerTabsByConversation 的更新要等到下一次 commit 才会反映到下面
      // 持久化 effect 的闭包里；这次 commit 里持久化 effect仍会用旧的（未恢复）
      // state 跑一遍，若不跳过就会把刚读出来的缓存原样写回一个空对象，抹掉
      // localStorage 里的记录（表现为刷新后 agent2ui 生成的网页再也找不回来）。
      skipNextDrawerSaveRef.current = true;
      setDrawerTabsByConversation((current) => ({ ...restored, ...current }));
    }
  }, [conversations]);

  // activeConvId 可能在上面的恢复 effect 之后才就位（例如刷新后没有已存的
  // active 会话，由 useChatRuntime 异步选出第一个会话）。单独用一个 effect
  // 盯着 activeConvId + 恢复出的 tabs，一旦两者都到位且该会话有非空 tab，
  // 就重新打开 drawer；否则内容虽已恢复但因 drawerOpen 重置为 false 而不可见。
  // 用 autoOpenAttemptedRef 保证每个会话只自动展开一次，避免用户手动关闭
  // drawer 后又被重新弹开。
  useEffect(() => {
    if (!activeDrawerKey || autoOpenAttemptedRef.current.has(activeDrawerKey)) {
      return;
    }
    const tabs = drawerTabsByConversation[activeDrawerKey]?.tabs;
    if (tabs === undefined) {
      return;
    }
    autoOpenAttemptedRef.current.add(activeDrawerKey);
    if (tabs.length > 0) {
      setDrawerOpen(true);
    }
  }, [activeDrawerKey, drawerTabsByConversation]);

  // drawer tab 状态变化时持久化（按当前会话集合裁剪）。会话列表还没加载完成时
  // （刚 mount，conversations 为空）不写入，否则会把 localStorage 里已有的
  // 缓存当成"用户没有任何 tab"直接删掉，抢在恢复 effect 读取之前就清空数据。
  useEffect(() => {
    if (conversations.length === 0) {
      return;
    }
    if (skipNextDrawerSaveRef.current) {
      skipNextDrawerSaveRef.current = false;
      return;
    }
    saveCachedDrawerTabs(
      DRAWER_TABS_USER_ID,
      drawerTabsByConversation,
      conversations.map((conversation) => drawerCacheKey(conversation)),
    );
  }, [conversations, drawerTabsByConversation]);

  function setActiveDrawerTab(tab: CachedWorkflowDrawerTab) {
    if (!activeDrawerKey) return;
    setDrawerTabsByConversation((current) => {
      const existing = current[activeDrawerKey] || { activeTab: "workflow" as const, tabs: [] };
      return { ...current, [activeDrawerKey]: { ...existing, activeTab: tab } };
    });
  }

  function openFile(path: string) {
    const normalized = path.trim();
    if (!normalized || !activeDrawerKey) return;
    const tabId = drawerTabId(normalized);
    setDrawerOpen(true);
    setDrawerTabsByConversation((current) => {
      const existing = current[activeDrawerKey] || { activeTab: "workflow" as const, tabs: [] };
      const hasTab = existing.tabs.some((tab) => tab.id === tabId);
      const tabs = hasTab
        ? existing.tabs
        : [
            ...existing.tabs,
            {
              id: tabId,
              path: normalized,
              title: normalized.split("/").filter(Boolean).pop() || normalized,
            },
          ];
      return { ...current, [activeDrawerKey]: { activeTab: tabId, tabs } };
    });
    // 已存在的 tab 再次打开时强制刷新内容（文件可能已被 agent 重写）。
    setRefreshNonceByTab((current) => ({ ...current, [tabId]: (current[tabId] || 0) + 1 }));
  }

  // 任务完成产出 HTML 时自动打开 drawer 文件预览。
  const pendingPreview = activeConvId ? pendingHtmlPreviews[activeConvId] : undefined;
  useEffect(() => {
    if (!pendingPreview) return;
    openFile(pendingPreview.path);
    consumePendingHtmlPreview(pendingPreview.conversationId, pendingPreview.messageId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingPreview]);

  const activeDrawerTabs = activeDrawerKey ? drawerTabsByConversation[activeDrawerKey] : undefined;
  const toggleDrawer = () => setDrawerOpen((current) => !current);
  const toggleSidebar = () => setSidebarCollapsed((current) => !current);
  const toggleDrawerFullscreen = () => setDrawerFullscreen((current) => !current);

  return (
    <div className="relative min-h-0 flex-1 overflow-hidden">
      <ChatLayout
        sidebar={
          <SessionListPane
            drawerOpen={drawerOpen}
            onToggleDrawer={toggleDrawer}
            onToggleSidebar={toggleSidebar}
          />
        }
        sidebarCollapsed={sidebarCollapsed}
        onToggleSidebar={toggleSidebar}
        drawerOpen={drawerOpen}
        drawerFullscreen={drawerFullscreen}
        drawer={
          <ChatDrawer
            conversation={activeConversation}
            tabs={activeDrawerTabs?.tabs || []}
            activeTab={activeDrawerTabs?.activeTab || "workflow"}
            refreshNonceByTab={refreshNonceByTab}
            onSelectTab={setActiveDrawerTab}
            onClose={() => setDrawerOpen(false)}
            fullscreen={drawerFullscreen}
            onToggleFullscreen={toggleDrawerFullscreen}
          />
        }
      >
        <MessageStream onOpenFile={openFile} />
        {activeConversation ? <RunStateIndicator conversation={activeConversation} /> : null}
        <Composer />
      </ChatLayout>
    </div>
  );
}
