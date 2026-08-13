export interface FloorAabbNode {
  id: string;
  x: number;
  y: number;
  width: number;
  depth: number;
}

export interface Point2 {
  x: number;
  y: number;
}

export interface QuaternionLike {
  x: number;
  y: number;
  z: number;
  w: number;
}

export interface Vector3Like {
  x: number;
  y: number;
  z: number;
}

export interface VrRayHitLike {
  distance: number;
  point: Vector3Like;
}

export const VR_RAY_MAX_DISTANCE = 3;
export const VR_SELECTION_HIGHLIGHT_KEY = "vrSelectionHighlightEnabled";
export const DEFAULT_VR_SELECTION_HIGHLIGHT = true;
export const FURNITURE_PANEL_EXIT_BUFFER_CM = 25;
export const FURNITURE_PANEL_REACH_MULTIPLIER = 1.5;

export interface FurniturePanelNode extends FloorAabbNode {
  reach?: number;
  uiWindow?: { provider?: string };
}

export interface FurniturePanelProximityState {
  visible: ReadonlySet<string>;
  dismissed: ReadonlySet<string>;
}

export interface FurniturePanelLayout {
  width: number;
  height: number;
  textureWidth: number;
  textureHeight: number;
  maxLines: number;
  closePosition: Vector3Like;
  actionY: number;
}

const STANDARD_FURNITURE_PANEL_LAYOUT: FurniturePanelLayout = {
  width: 1.5,
  height: 0.59,
  textureWidth: 768,
  textureHeight: 300,
  maxLines: 5,
  closePosition: { x: 0.63, y: 0.22, z: 0.012 },
  actionY: -0.39,
};

const PLANS_FURNITURE_PANEL_LAYOUT: FurniturePanelLayout = {
  width: 1.5,
  height: 0.95,
  textureWidth: 768,
  textureHeight: 486,
  maxLines: 8,
  closePosition: { x: 0.63, y: 0.4, z: 0.012 },
  actionY: -0.37,
};

const BROWSER_FURNITURE_PANEL_LAYOUT: FurniturePanelLayout = {
  width: 1.6,
  height: 0.92,
  textureWidth: 768,
  textureHeight: 512,
  maxLines: 7,
  closePosition: { x: 0.68, y: 0.39, z: 0.012 },
  actionY: -0.4,
};

export function furniturePanelLayout(provider?: string): FurniturePanelLayout {
  if (["plan_status", "google_gmail_unread", "google_calendar_status", "transient_email"].includes(provider || "")) {
    return PLANS_FURNITURE_PANEL_LAYOUT;
  }
  if (provider === "browser_status") return BROWSER_FURNITURE_PANEL_LAYOUT;
  return STANDARD_FURNITURE_PANEL_LAYOUT;
}

export interface FurniturePanelDecisionTarget {
  collection: "plans" | "skills";
  id: string;
}

export interface FurniturePanelContent {
  lines: string[];
  decision: FurniturePanelDecisionTarget | null;
  /** Optional screenshot/panel image rendered above the text lines. */
  imageUrl?: string | null;
}

export type VrPanelAction =
  | { kind: "navigate"; id: string; label: string; targetPageId: string }
  | {
    kind: "decision";
    id: string;
    label: string;
    collection: "plans" | "skills";
    targetId: string;
    decision: "approved" | "declined";
  };

export interface VrPanelPage {
  id: string;
  title: string;
  lines: string[];
  actions: VrPanelAction[];
}

export interface VrPanelDocument {
  rootPageId: string;
  pages: VrPanelPage[];
}

export interface VrPanelHistoryEntry {
  pageId: string;
  scroll: number;
}

export interface VrPanelViewState {
  pageId: string;
  history: VrPanelHistoryEntry[];
  scroll: number;
}

export function createPanelViewState(document: VrPanelDocument): VrPanelViewState {
  return { pageId: document.rootPageId, history: [], scroll: 0 };
}

export function reconcilePanelViewState(
  document: VrPanelDocument,
  state: VrPanelViewState | undefined,
): VrPanelViewState {
  const pageIds = new Set(document.pages.map((page) => page.id));
  if (!state || !pageIds.has(state.pageId)) return createPanelViewState(document);
  return {
    pageId: state.pageId,
    scroll: Math.max(0, state.scroll),
    history: state.history.filter((entry) => pageIds.has(entry.pageId)),
  };
}

export function navigatePanel(
  document: VrPanelDocument,
  state: VrPanelViewState,
  targetPageId: string,
): VrPanelViewState {
  if (!document.pages.some((page) => page.id === targetPageId)) return state;
  return {
    pageId: targetPageId,
    scroll: 0,
    history: [...state.history, { pageId: state.pageId, scroll: state.scroll }],
  };
}

export function backPanel(state: VrPanelViewState): VrPanelViewState {
  const previous = state.history[state.history.length - 1];
  return previous
    ? { pageId: previous.pageId, scroll: previous.scroll, history: state.history.slice(0, -1) }
    : state;
}

function wrapOnePanelLine(value: string, maxChars: number): string[] {
  const text = value.trim();
  if (!text) return [""];
  const words = text.split(/\s+/);
  const output: string[] = [];
  let current = "";
  const flush = () => {
    if (current) output.push(current);
    current = "";
  };
  for (const original of words) {
    let word = original;
    if (word.length > maxChars) {
      flush();
      while (word.length > maxChars) {
        output.push(word.slice(0, maxChars));
        word = word.slice(maxChars);
      }
    }
    if (!word) continue;
    if (!current) current = word;
    else if (current.length + word.length + 1 <= maxChars) current += ` ${word}`;
    else {
      flush();
      current = word;
    }
  }
  flush();
  return output.length ? output : [""];
}

export function wrappedPanelLines(lines: readonly string[], maxChars = 48): string[] {
  return lines.flatMap((line) => String(line).split(/\r?\n/).flatMap((part) => (
    wrapOnePanelLine(part, maxChars)
  )));
}

export function panelViewport(
  document: VrPanelDocument,
  state: VrPanelViewState,
  maxLines: number,
) {
  const page = document.pages.find((item) => item.id === state.pageId)
    || document.pages[0];
  const content = wrappedPanelLines(page?.lines || []);
  const availableSlots = Math.max(1, maxLines - 1);
  const contentSlots = content.length > availableSlots
    ? Math.max(1, availableSlots - 1)
    : availableSlots;
  const maxScroll = Math.max(0, content.length - contentSlots);
  const scroll = Math.min(Math.max(0, state.scroll), maxScroll);
  const visible = content.slice(scroll, scroll + contentSlots);
  if (maxScroll > 0) {
    visible.push(`↕ ${scroll + 1}-${Math.min(
      content.length, scroll + contentSlots,
    )}/${content.length}`);
  }
  return { page, lines: [page?.title || "Panel", ...visible], scroll, maxScroll };
}

export function scrollPanel(
  document: VrPanelDocument,
  state: VrPanelViewState,
  maxLines: number,
  delta: number,
): VrPanelViewState {
  const viewport = panelViewport(document, state, maxLines);
  return { ...state, scroll: Math.min(viewport.maxScroll, Math.max(0, viewport.scroll + delta)) };
}

export function panelScrollRepeatStep(
  axis: number,
  direction: number,
  nextAt: number,
  now: number,
  threshold = 0.55,
  initialDelay = 350,
  repeatDelay = 120,
) {
  const nextDirection = Math.abs(axis) >= threshold ? (axis > 0 ? 1 : -1) : 0;
  if (!nextDirection) return { delta: 0, direction: 0, nextAt: 0 };
  if (nextDirection !== direction) {
    return { delta: nextDirection, direction: nextDirection, nextAt: now + initialDelay };
  }
  if (now >= nextAt) {
    return { delta: nextDirection, direction: nextDirection, nextAt: now + repeatDelay };
  }
  return { delta: 0, direction, nextAt };
}

function recordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : [];
}

function displayTime(value: unknown) {
  if (typeof value !== "string" || !value) return "";
  return value.replace("T", " ").replace(/([+-]\d\d:\d\d|Z)$/, "");
}

/** Build the multi-page content for upgraded furniture, while wrapping every
 * existing furniture provider in a compatible single-page document. */
export function furniturePanelDocument(
  provider: string | undefined,
  liveData: Record<string, unknown> | null,
  title: string,
  body = "",
): VrPanelDocument {
  if (provider === "google_gmail_unread") {
    const messages = recordArray(liveData?.messages).slice(0, 3);
    const root: VrPanelPage = {
      id: "root", title, actions: [],
      lines: messages.length ? [body, ...messages.map((message, index) => (
        `${index + 1}. ${String(message.subject || "(no subject)")} — ${String(message.sender || "Unknown sender")} ${String(message.received_at || "")}`
      ))] : [body, typeof liveData?.error === "string" ? liveData.error : "No recent inbox messages."],
    };
    const pages = messages.map((message, index): VrPanelPage => ({
      id: `message-${String(message.message_id || index)}`,
      title: String(message.subject || "(no subject)"),
      lines: [
        `From: ${String(message.sender || "Unknown sender")}`,
        `Received: ${String(message.received_at || "Unknown")}`,
        String(message.snippet || "No preview is available."),
      ],
      actions: [],
    }));
    root.actions = pages.map((page, index) => ({
      kind: "navigate", id: `mail-${index}`, label: `Open ${index + 1}`, targetPageId: page.id,
    }));
    return { rootPageId: "root", pages: [root, ...pages] };
  }
  if (provider === "google_calendar_status") {
    const events = recordArray(liveData?.events);
    const pages: VrPanelPage[] = [];
    const chunks = events.length ? Array.from({ length: Math.ceil(events.length / 2) }, (_, index) => (
      events.slice(index * 2, index * 2 + 2)
    )) : [[]];
    chunks.forEach((chunk, pageIndex) => {
      const id = `events-${pageIndex}`;
      const actions: VrPanelAction[] = chunk.map((event, index) => ({
        kind: "navigate", id: `event-${pageIndex}-${index}`,
        label: `Open ${pageIndex * 2 + index + 1}`,
        targetPageId: `event-${String(event.event_id || `${pageIndex}-${index}`)}`,
      }));
      if (pageIndex + 1 < chunks.length) actions.push({
        kind: "navigate", id: `events-next-${pageIndex}`, label: "Next", targetPageId: `events-${pageIndex + 1}`,
      });
      pages.push({
        id, title: pageIndex ? `${title} · ${pageIndex + 1}` : title,
        lines: chunk.length ? chunk.flatMap((event, index) => [
          `${pageIndex * 2 + index + 1}. ${String(event.title || "(untitled event)")}`,
          `${displayTime(event.start)}${event.all_day === true ? " · all day" : ""}`,
        ]) : [typeof liveData?.error === "string" ? liveData.error : "No events in the next seven days."],
        actions,
      });
      chunk.forEach((event, index) => pages.push({
        id: `event-${String(event.event_id || `${pageIndex}-${index}`)}`,
        title: String(event.title || "(untitled event)"),
        lines: [
          `Start: ${displayTime(event.start) || "Unknown"}`,
          `End: ${displayTime(event.end) || "Unknown"}`,
          ...(event.all_day === true ? ["All-day event"] : []),
          ...(event.location ? [`Location: ${String(event.location)}`] : []),
          ...(event.description ? [String(event.description)] : ["No description."]),
        ], actions: [],
      }));
    });
    return { rootPageId: "events-0", pages };
  }
  if (provider === "plan_status") {
    const details = recordArray(liveData?.plan_details);
    const authorizations = recordArray(liveData?.pending).filter((item) => (
      String(item.plan_id || "").startsWith("authz-")
    ));
    const entries: Record<string, unknown>[] = [
      ...details.map((item) => ({ ...item, entry_type: "plan" })),
      ...authorizations.map((item) => ({ ...item, entry_type: "authorization" })),
    ];
    entries.sort((left, right) => String(
      right.updated_at || right.created_at || "",
    ).localeCompare(String(left.updated_at || left.created_at || "")));
    const pages: VrPanelPage[] = [];
    const chunks = entries.length ? Array.from({ length: Math.ceil(entries.length / 2) }, (_, index) => (
      entries.slice(index * 2, index * 2 + 2)
    )) : [[]];
    chunks.forEach((chunk, pageIndex) => {
      const actions: VrPanelAction[] = chunk.map((entry, index) => ({
        kind: "navigate", id: `plan-open-${pageIndex}-${index}`,
        label: `Open ${pageIndex * 2 + index + 1}`,
        targetPageId: `plan-${String(entry.plan_id || `${pageIndex}-${index}`)}`,
      }));
      if (pageIndex + 1 < chunks.length) actions.push({
        kind: "navigate", id: `plans-next-${pageIndex}`, label: "Next", targetPageId: `plans-${pageIndex + 1}`,
      });
      pages.push({
        id: `plans-${pageIndex}`, title: pageIndex ? `${title} · ${pageIndex + 1}` : title,
        lines: chunk.length ? chunk.flatMap((entry, index) => [
          `${pageIndex * 2 + index + 1}. ${String(entry.title || entry.plan_id || "Untitled plan")}`,
          `${entry.entry_type === "authorization" ? "Authorization" : "Plan"} · ${decisionStatusLabel(entry.status) || "Pending"}`,
        ]) : ["No plans have been saved."], actions,
      });
      chunk.forEach((entry, index) => {
        const targetId = String(entry.plan_id || `${pageIndex}-${index}`);
        const pending = String(entry.status || "pending") === "pending";
        const detailActions: VrPanelAction[] = pending ? (["approved", "declined"] as const).map((decision) => ({
          kind: "decision", id: `plan-${targetId}-${decision}`,
          label: decision === "approved" ? "Approve" : "Decline",
          collection: "plans", targetId, decision,
        })) : [];
        pages.push({
          id: `plan-${targetId}`, title: String(entry.title || targetId),
          lines: entry.entry_type === "authorization" ? [
            "Authorization request", `Status: ${decisionStatusLabel(entry.status) || "Pending"}`,
          ] : [
            `Status: ${decisionStatusLabel(entry.status)}`,
            `Revision: ${String(entry.revision || 1)}`,
            `Goal: ${String(entry.goal || "")}`,
            ...(Array.isArray(entry.steps) ? entry.steps.map((step: unknown, stepIndex: number) => `${stepIndex + 1}. ${String(step)}`) : []),
            ...(entry.notes ? [`Notes: ${String(entry.notes)}`] : []),
            `Created: ${displayTime(entry.created_at)}`,
            `Updated: ${displayTime(entry.updated_at)}`,
            ...(entry.decided_at ? [`Decided: ${displayTime(entry.decided_at)}`] : []),
            ...(entry.withdraw_reason ? [`Withdrawn: ${String(entry.withdraw_reason)}`] : []),
          ], actions: detailActions,
        });
      });
    });
    return { rootPageId: "plans-0", pages };
  }
  const content = furniturePanelContent(provider, liveData);
  return {
    rootPageId: "root",
    pages: [{ id: "root", title, lines: [body, ...content.lines], actions: content.decision ? (["approved", "declined"] as const).map((decision) => ({
      kind: "decision", id: `${content.decision!.collection}-${decision}`,
      label: decision === "approved" ? "Approve" : "Decline",
      collection: content.decision!.collection,
      targetId: content.decision!.id,
      decision,
    })) : [] }],
  };
}

const DECISION_STATUS_LABELS: Record<string, string> = {
  approved: "Approved",
  declined: "Declined",
  withdrawn: "Withdrawn",
};

function decisionStatusLabel(status: unknown) {
  if (typeof status === "string" && DECISION_STATUS_LABELS[status]) {
    return DECISION_STATUS_LABELS[status];
  }
  if (typeof status === "string" && status) {
    return `${status.charAt(0).toUpperCase()}${status.slice(1)}`;
  }
  return "";
}

function furnitureItemTitle(item: Record<string, unknown>, idKey: string, titleKey: string) {
  const title = item[titleKey];
  if (typeof title === "string" && title) return title;
  const id = item[idKey];
  return typeof id === "string" ? id : "";
}

function genericFurniturePanelLines(liveData: Record<string, unknown> | null): string[] {
  if (!liveData) return [];
  return Object.entries(liveData).slice(0, 4).map(([key, item]) => {
    if (typeof item === "string" || typeof item === "number" || typeof item === "boolean") {
      return `${key}: ${String(item)}`;
    }
    if (Array.isArray(item)) return `${key}: ${item.length} item(s)`;
    return `${key}: available`;
  });
}

function browserTextLines(text: string, maxLines: number, maxChars: number): string[] {
  const words = text.replace(/\s+/g, " ").trim().split(" ");
  const lines: string[] = [];
  let current = "";
  for (const word of words) {
    if (current && current.length + 1 + word.length > maxChars) {
      lines.push(current);
      current = "";
      if (lines.length === maxLines) break;
    }
    current = current ? `${current} ${word}` : word;
    if (current.length > maxChars) {
      lines.push(current);
      current = "";
      if (lines.length === maxLines) break;
    }
  }
  if (current && lines.length < maxLines) lines.push(current);
  return lines;
}

/**
 * Turn a furniture window snapshot into panel text and, for approval
 * providers, the next decision the user can make. Plan and skill windows
 * render their own pending/recent lines so the desk reacts visibly when a
 * plan has just been approved or declined.
 */
export function furniturePanelContent(
  provider: string | undefined,
  liveData: Record<string, unknown> | null,
): FurniturePanelContent {
  if (!liveData || typeof liveData !== "object") {
    return { lines: [], decision: null };
  }
  if (provider === "browser_status") {
    const tool = typeof liveData.tool === "string" && liveData.tool ? liveData.tool : "";
    const url = typeof liveData.url === "string" && liveData.url ? liveData.url : "";
    const updatedAt = typeof liveData.updated_at === "string" && liveData.updated_at
      ? liveData.updated_at.replace("T", " ").replace("+00:00", " UTC")
      : "";
    const text = typeof liveData.text === "string" ? liveData.text.trim() : "";
    const lines: string[] = [];
    if (tool) lines.push(`Tool: ${tool}`);
    if (url) lines.push(`URL: ${url}`);
    if (updatedAt) lines.push(`Updated: ${updatedAt}`);
    if (text) lines.push(...browserTextLines(text, 4, 52));
    if (lines.length === 0) lines.push("No browser activity yet.");
    return {
      lines,
      decision: null,
      imageUrl: liveData.has_image === true ? "/api/furniture/browser/image" : null,
    };
  }
  if (provider !== "plan_status" && provider !== "skill_status") {
    return { lines: genericFurniturePanelLines(liveData), decision: null };
  }

  const collection = provider === "plan_status" ? "plans" as const : "skills" as const;
  const noun = collection === "plans" ? "plan" : "skill";
  const pending = Array.isArray(liveData.pending)
    ? liveData.pending.filter((item): item is Record<string, unknown> => (
      Boolean(item) && typeof item === "object"
    ))
    : [];
  const pendingCount = Number.isFinite(liveData.pending_count)
    ? Number(liveData.pending_count)
    : pending.length;
  const firstPending = pending[0];
  const decisionId = collection === "plans"
    ? firstPending?.plan_id
    : firstPending?.slug;
  const decision = typeof decisionId === "string" && decisionId
    ? { collection, id: decisionId }
    : null;

  if (liveData.available === false && typeof liveData.message === "string") {
    return {
      lines: [`${noun === "plan" ? "Plans" : "Skills"} unavailable: ${liveData.message}`],
      decision,
    };
  }

  const lines: string[] = [];
  if (collection === "plans") {
    lines.push(`Pending plans: ${pendingCount}`);
    pending.slice(0, 2).forEach((item) => {
      lines.push(`• ${furnitureItemTitle(item, "plan_id", "title")}`);
    });
    const recent = liveData.recent && typeof liveData.recent === "object"
      ? liveData.recent as Record<string, unknown>
      : null;
    if (recent) {
      const status = decisionStatusLabel(recent.status);
      lines.push(
        `Recent: ${furnitureItemTitle(recent, "plan_id", "title")}${status ? ` — ${status}` : ""}`,
      );
    } else {
      lines.push("Recent: none");
    }
  } else {
    lines.push(`Pending skills: ${pendingCount}`);
    if (firstPending) {
      lines.push(`• ${furnitureItemTitle(firstPending, "slug", "name")}`);
    } else {
      const activeCount = Number.isFinite(liveData.active_count)
        ? Number(liveData.active_count)
        : 0;
      lines.push(`Active skills: ${activeCount}`);
    }
  }
  return { lines, decision };
}

/** Keep wall-mounted panels facing into the room without tracking the viewer.
 * In floor coordinates the north/mailbox wall is the low-y half. */
export function furniturePanelRotationY(nodeY: number, roomDepth: number) {
  return nodeY < roomDepth / 2 ? Math.PI : 0;
}

export function furnitureInteractionReach(
  node: Pick<FurniturePanelNode, "width" | "depth" | "reach">,
  reachCeiling: number,
) {
  const derived = 60 + Math.max(node.width, node.depth) / 2;
  const declared = typeof node.reach === "number" && Number.isFinite(node.reach) && node.reach > 0
    ? node.reach
    : derived;
  return Math.min(reachCeiling, declared) * FURNITURE_PANEL_REACH_MULTIPLIER;
}

function withinFurnitureDistance(
  node: FurniturePanelNode,
  point: Point2 | null,
  limit: number,
) {
  return point !== null && Math.hypot(point.x - node.x, point.y - node.y) <= limit;
}

/**
 * Derive the next set of proximity-driven panels. Visible panels receive an
 * exit buffer, while manually dismissed panels stay suppressed until both the
 * user and agent have left that buffered area.
 */
export function updateFurniturePanelProximity(
  nodes: readonly FurniturePanelNode[],
  user: Point2 | null,
  agent: Point2 | null,
  reachCeiling: number,
  state: FurniturePanelProximityState,
  exitBuffer = FURNITURE_PANEL_EXIT_BUFFER_CM,
): FurniturePanelProximityState {
  const visible = new Set<string>();
  const nodeIds = new Set(nodes.map((node) => node.id));
  const dismissed = new Set([...state.dismissed].filter((id) => nodeIds.has(id)));

  nodes.forEach((node) => {
    const enterLimit = furnitureInteractionReach(node, reachCeiling);
    const exitLimit = enterLimit + exitBuffer;
    const withinEnter = (
      withinFurnitureDistance(node, user, enterLimit) ||
      withinFurnitureDistance(node, agent, enterLimit)
    );
    const withinExit = (
      withinFurnitureDistance(node, user, exitLimit) ||
      withinFurnitureDistance(node, agent, exitLimit)
    );

    if (dismissed.has(node.id)) {
      if (withinExit) return;
      dismissed.delete(node.id);
    }
    if (withinEnter || (state.visible.has(node.id) && withinExit)) {
      visible.add(node.id);
    }
  });

  return { visible, dismissed };
}

export function parseVrSelectionHighlight(stored: string | null) {
  if (stored === null) return DEFAULT_VR_SELECTION_HIGHLIGHT;
  try {
    return JSON.parse(stored) === true;
  } catch (_error) {
    return DEFAULT_VR_SELECTION_HIGHLIGHT;
  }
}

export function loadVrSelectionHighlight() {
  try {
    return parseVrSelectionHighlight(localStorage.getItem(VR_SELECTION_HIGHLIGHT_KEY));
  } catch (_error) {
    return DEFAULT_VR_SELECTION_HIGHLIGHT;
  }
}

export function transientTriggerBehavior(kind: "panel" | "email_card", body?: string) {
  return kind === "email_card" && Boolean(body) ? "page" as const : "none" as const;
}

export function nextEmailPage(currentPage: number, bodyLength: number, pageSize = 800) {
  const pages = Math.max(1, Math.ceil(bodyLength / pageSize));
  return (currentPage + 1) % (pages + 1);
}

export interface HandedInputSource {
  handedness: string;
  gamepad?: Gamepad | null;
}

export interface FurnitureDecalNode extends FloorAabbNode {
  height: number;
}

export function inputSourcesByHand<T extends HandedInputSource>(sources: Iterable<T>) {
  const ordered = [...sources];
  return {
    left: ordered.find((source) => source.handedness === "left"),
    right: ordered.find((source) => source.handedness === "right"),
  };
}

/** The outer WebXR ArrayCamera follows the locomotion rig; an eye camera
 * carries the live headset pose. Either eye has the same useful facing vector. */
export function viewerPoseCamera(xrCamera: ArrayCamera): Camera {
  return xrCamera.cameras[0] || xrCamera;
}

function rotateVector(
  vector: { x: number; y: number; z: number },
  quaternion: QuaternionLike,
) {
  const uv = {
    x: quaternion.y * vector.z - quaternion.z * vector.y,
    y: quaternion.z * vector.x - quaternion.x * vector.z,
    z: quaternion.x * vector.y - quaternion.y * vector.x,
  };
  const uuv = {
    x: quaternion.y * uv.z - quaternion.z * uv.y,
    y: quaternion.z * uv.x - quaternion.x * uv.z,
    z: quaternion.x * uv.y - quaternion.y * uv.x,
  };
  return {
    x: vector.x + 2 * (quaternion.w * uv.x + uuv.x),
    y: vector.y + 2 * (quaternion.w * uv.y + uuv.y),
    z: vector.z + 2 * (quaternion.w * uv.z + uuv.z),
  };
}

/** Current-frame headset forward from XR reference space into scene space. */
export function viewerForwardFromPose(
  viewerOrientation: QuaternionLike,
  rigOrientation?: QuaternionLike,
) {
  let forward = rotateVector({ x: 0, y: 0, z: -1 }, viewerOrientation);
  if (rigOrientation) forward = rotateVector(forward, rigOrientation);
  const magnitude = Math.hypot(forward.x, forward.z);
  return magnitude
    ? { x: forward.x / magnitude, z: forward.z / magnitude }
    : { x: 0, z: 0 };
}

/** Compose the current XRFrame viewer position with the locomotion rig. The
 * renderer camera is updated later in the frame, so it cannot authoritatively
 * drive same-frame proximity checks. */
export function viewerWorldPositionFromPose(
  viewerPosition: Vector3Like,
  rigPosition: Vector3Like,
  rigOrientation: QuaternionLike,
) {
  const rotated = rotateVector(viewerPosition, rigOrientation);
  return {
    x: rigPosition.x + rotated.x,
    y: rigPosition.y + rotated.y,
    z: rigPosition.z + rotated.z,
  };
}

/**
 * Resolve the nearest physical ray hit and reject it when it falls outside the
 * active pointing ray's forward horizontal hemisphere. Callers must provide
 * the same live origin and direction used for the raycast so physical turns do
 * not get compared with a stale headset orientation. Only the nearest surface
 * is ever returned, so callers cannot skip an occluder behind it.
 */
export function selectVrRayHit<T extends VrRayHitLike>(
  hits: readonly T[],
  viewerPosition: Vector3Like,
  viewerForward: Vector3Like,
  maxDistance = VR_RAY_MAX_DISTANCE,
): T | null {
  if (!hits.length || !Number.isFinite(maxDistance) || maxDistance < 0) return null;
  const nearest = hits.reduce((closest, hit) => (
    hit.distance < closest.distance ? hit : closest
  ));
  if (!Number.isFinite(nearest.distance) || nearest.distance < 0 || nearest.distance > maxDistance) return null;

  const forwardMagnitude = Math.hypot(viewerForward.x, viewerForward.z);
  if (!forwardMagnitude) return null;
  const targetX = nearest.point.x - viewerPosition.x;
  const targetZ = nearest.point.z - viewerPosition.z;
  const horizontalDot = (
    targetX * viewerForward.x + targetZ * viewerForward.z
  ) / forwardMagnitude;

  // A zero dot product is the exact side boundary and remains selectable.
  return horizontalDot >= -Number.EPSILON ? nearest : null;
}

export function isRayGrabbableNode(
  node: { type?: string; interaction?: { grabbable?: boolean } } | undefined,
): node is { type: "prop"; interaction: { grabbable: true } } {
  return node?.type === "prop" && node.interaction?.grabbable === true;
}

/** Normalize WebXR's xr-standard primary stick into semantic room axes.
 * Axis 2 is left/right; axis 3 is down/forward-negative. */
export function questLocomotionStick(axis2: number, axis3: number): Point2 {
  return { x: axis2, y: axis3 === 0 ? 0 : -axis3 };
}

export function furnitureCenterSceneY(elevation: number, unitsPerMeter = 100) {
  return elevation / unitsPerMeter;
}

export function furnitureDecalLayouts(
  node: FurnitureDecalNode,
  image: { width: number; height: number },
  offset = 0.5,
) {
  const safeImageWidth = Math.max(1, image.width);
  const safeImageHeight = Math.max(1, image.height);
  const contain = (faceWidth: number) => {
    const scale = Math.min(
      faceWidth / safeImageWidth,
      node.height / safeImageHeight,
    );
    return {
      width: safeImageWidth * scale,
      height: safeImageHeight * scale,
    };
  };
  const xFace = contain(node.depth);
  const zFace = contain(node.width);
  return [
    { ...xFace, x: node.width / 2 + offset, z: 0, rotationY: Math.PI / 2, face: "+x" },
    { ...xFace, x: -node.width / 2 - offset, z: 0, rotationY: -Math.PI / 2, face: "-x" },
    { ...zFace, x: 0, z: node.depth / 2 + offset, rotationY: 0, face: "+z" },
    { ...zFace, x: 0, z: -node.depth / 2 - offset, rotationY: Math.PI, face: "-z" },
  ] as const;
}

/** Wrap complete caption text without dropping long words or unspaced scripts. */
export function wrapCaptionText(value: string, maxCharacters = 42) {
  const words = value.trim().split(/\s+/).filter(Boolean);
  if (!words.length || maxCharacters < 2) return [];

  const lines: string[] = [];
  let current = "";
  for (const word of words) {
    const fragments = word.length > maxCharacters
      ? word.match(new RegExp(`.{1,${maxCharacters}}`, "g")) || [word]
      : [word];
    for (const fragment of fragments) {
      const candidate = current ? `${current} ${fragment}` : fragment;
      if (candidate.length <= maxCharacters) {
        current = candidate;
        continue;
      }
      if (current) lines.push(current);
      current = fragment;
    }
  }
  if (current) lines.push(current);
  return lines;
}

/** Select the three-line window corresponding to actual audio progress. */
export function captionWindowAtProgress(
  value: string,
  progress: number,
  maxCharacters = 42,
  maxLines = 3,
) {
  if (maxLines < 1) return [];
  const lines = wrapCaptionText(value, maxCharacters);
  if (lines.length <= maxLines) return lines;

  const ratio = Number.isFinite(progress) ? Math.min(1, Math.max(0, progress)) : 0;
  if (ratio === 0) return lines.slice(0, maxLines);
  if (ratio === 1) return lines.slice(-maxLines);

  const totalCharacters = lines.reduce((total, line) => total + Math.max(1, line.length), 0);
  const target = totalCharacters * ratio;
  let elapsed = 0;
  let currentLine = 0;
  for (let index = 0; index < lines.length; index += 1) {
    elapsed += Math.max(1, lines[index].length);
    if (elapsed >= target) {
      currentLine = index;
      break;
    }
  }
  const maxStart = lines.length - maxLines;
  const start = Math.min(maxStart, Math.max(0, currentLine - Math.floor(maxLines / 2)));
  return lines.slice(start, start + maxLines);
}

/** Backward-compatible tail window for non-progressive callers. */
export function clampCaptionText(value: string, maxCharacters = 42, maxLines = 3) {
  const lines = wrapCaptionText(value, maxCharacters);
  if (maxLines < 1) return [];

  const visible = lines.slice(-maxLines);
  if (lines.length > maxLines && visible.length) {
    visible[0] = `…${visible[0].slice(-(maxCharacters - 1))}`;
  }
  return visible;
}

export function browserEndpoints(location: Pick<Location, "protocol" | "host">) {
  const secure = location.protocol === "https:";
  return {
    baseUrl: `${secure ? "https" : "http"}://${location.host}`,
    wsUrl: `${secure ? "wss" : "ws"}://${location.host}/client-ws`,
  };
}

export function withRendererMode(url: string, mode: "normal" | "vr", base: string) {
  const parsed = new URL(url, base);
  parsed.searchParams.set("renderer_mode", mode);
  return parsed.toString();
}

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

export function validateVrStageShape(value: unknown) {
  if (!value || typeof value !== "object") return false;
  const stage = value as Record<string, unknown>;
  if (stage.version !== 3 || stage.mode !== "vr" || !stage.room || !stage.world || !Array.isArray(stage.nodes) || stage.nodes.length > 80) return false;
  const room = stage.room as Record<string, unknown>;
  const world = stage.world as Record<string, unknown>;
  if (![room.width, room.depth, room.height].every(finite) ||
      ![world.units_per_meter, world.reach_ceiling, world.move_speed].every(finite) ||
      (room.width as number) <= 0 || (room.depth as number) <= 0 || (room.height as number) <= 0 ||
      typeof room.floor_color !== "string" || typeof room.wall_color !== "string" ||
      !stage.metadata || typeof stage.metadata !== "object") return false;
  const ids = new Set<string>();
  const labels = new Set<string>();
  let avatars = 0;
  for (const rawNode of stage.nodes) {
    if (!rawNode || typeof rawNode !== "object") return false;
    const node = rawNode as Record<string, unknown>;
    if (typeof node.id !== "string" || !/^[A-Za-z0-9_-]{1,80}$/.test(node.id) || ids.has(node.id)) return false;
    ids.add(node.id);
    if (node.id === "avatar") {
      avatars += 1;
      if (node.type !== "avatar") return false;
    } else if (node.type !== "furniture" && node.type !== "prop") return false;
    if (node.type === "prop") {
      const interaction = node.interaction as Record<string, unknown> | undefined;
      if (interaction?.grabbable !== true || typeof node.support_id !== "string" || !finite(node.yaw)) return false;
    } else if ((node.interaction as Record<string, unknown> | undefined)?.grabbable !== undefined) return false;
    const label = typeof node.label === "string" && node.label ? node.label : node.id;
    if (labels.has(label.toLocaleLowerCase())) return false;
    labels.add(label.toLocaleLowerCase());
    if (![node.x, node.y, node.width, node.depth, node.height, node.elevation].every(finite)) return false;
    if ((node.width as number) <= 0 || (node.depth as number) <= 0 || (node.height as number) <= 0) return false;
    if ((node.x as number) - (node.width as number) / 2 < 0 ||
        (node.x as number) + (node.width as number) / 2 > (room.width as number) ||
        (node.y as number) - (node.depth as number) / 2 < 0 ||
        (node.y as number) + (node.depth as number) / 2 > (room.depth as number) ||
        (node.elevation as number) - (node.height as number) / 2 < 0 ||
        (node.elevation as number) + (node.height as number) / 2 > (room.height as number) ||
        typeof node.color !== "string") return false;
  }
  if (avatars !== 1) return false;
  const avatar = stage.nodes.find((node) => (node as Record<string, unknown>).id === "avatar") as unknown as FloorAabbNode;
  return !stage.nodes.some((node) => {
    const furniture = node as FloorAabbNode;
    return furniture.id !== "avatar" && pointInsideAabb(avatar, furniture);
  });
}

export function floorToScene(
  room: { width: number; depth: number },
  point: Point2,
  unitsPerMeter = 100,
) {
  return {
    x: (point.x - room.width / 2) / unitsPerMeter,
    z: (room.depth / 2 - point.y) / unitsPerMeter,
  };
}

export function rescaleThumbstick(x: number, y: number, deadZone = 0.2) {
  const magnitude = Math.hypot(x, y);
  if (magnitude <= deadZone) return { x: 0, y: 0 };
  const scaled = Math.min(1, (magnitude - deadZone) / (1 - deadZone));
  return { x: x / magnitude * scaled, y: y / magnitude * scaled };
}

export function headRelativeDelta(
  stick: Point2,
  horizontalFacing: { x: number; z: number },
  speed: number,
  elapsedSeconds: number,
) {
  const magnitude = Math.hypot(horizontalFacing.x, horizontalFacing.z);
  if (!magnitude) return { x: 0, z: 0 };
  const forwardX = horizontalFacing.x / magnitude;
  const forwardZ = horizontalFacing.z / magnitude;
  const rightX = -forwardZ;
  const rightZ = forwardX;
  return {
    x: (rightX * stick.x + forwardX * stick.y) * speed * elapsedSeconds,
    z: (rightZ * stick.x + forwardZ * stick.y) * speed * elapsedSeconds,
  };
}

export function snapTurnStep(
  axis: number,
  armed: boolean,
  threshold = 0.7,
  rearm = 0.3,
  angleRadians = Math.PI / 6,
) {
  if (armed && Math.abs(axis) >= threshold) {
    return { armed: false, delta: axis > 0 ? -angleRadians : angleRadians };
  }
  if (!armed && Math.abs(axis) <= rearm) return { armed: true, delta: 0 };
  return { armed, delta: 0 };
}

export const pressedEdge = (pressed: boolean, wasPressed: boolean) => pressed && !wasPressed;

export function pointInsideAabb(point: Point2, node: FloorAabbNode) {
  return point.x >= node.x - node.width / 2 && point.x <= node.x + node.width / 2 &&
    point.y >= node.y - node.depth / 2 && point.y <= node.y + node.depth / 2;
}

export function isViewerPositionClear(
  point: Point2,
  furniture: FloorAabbNode[],
  room: { width: number; depth: number },
  viewerRadius = 25,
) {
  return point.x >= viewerRadius && point.x <= room.width - viewerRadius &&
    point.y >= viewerRadius && point.y <= room.depth - viewerRadius &&
    !furniture.some((node) => (
      point.x >= node.x - node.width / 2 - viewerRadius &&
      point.x <= node.x + node.width / 2 + viewerRadius &&
      point.y >= node.y - node.depth / 2 - viewerRadius &&
      point.y <= node.y + node.depth / 2 + viewerRadius
    ));
}

export function selectViewerSpawn(
  avatar: FloorAabbNode,
  furniture: FloorAabbNode[],
  room: { width: number; depth: number },
  distance = 175,
  viewerRadius = 25,
) {
  const candidates = [
    { x: avatar.x + distance, y: avatar.y },
    { x: avatar.x - distance, y: avatar.y },
    { x: avatar.x, y: avatar.y + distance },
    { x: avatar.x, y: avatar.y - distance },
  ];
  const clear = candidates.find((point) => (
    isViewerPositionClear(point, furniture, room, viewerRadius)
  ));
  return clear || {
    x: Math.min(room.width - viewerRadius, Math.max(viewerRadius, avatar.x + distance)),
    y: Math.min(room.depth - viewerRadius, Math.max(viewerRadius, avatar.y)),
  };
}

export function selectDockingPoint(
  target: FloorAabbNode,
  avatar: FloorAabbNode,
  furniture: FloorAabbNode[],
  room: { width: number; depth: number },
  gap = 10,
) {
  const radius = Math.max(avatar.width, avatar.depth) / 2;
  const candidates = [
    { x: target.x, y: target.y - target.depth / 2 - radius - gap },
    { x: target.x + target.width / 2 + radius + gap, y: target.y },
    { x: target.x, y: target.y + target.depth / 2 + radius + gap },
    { x: target.x - target.width / 2 - radius - gap, y: target.y },
  ];
  return candidates
    .map((point, order) => ({ point, order }))
    .filter(({ point }) => (
      point.x >= radius && point.x <= room.width - radius &&
      point.y >= radius && point.y <= room.depth - radius &&
      !furniture.some((node) => node.id !== target.id && pointInsideAabb(point, node))
    ))
    .sort((a, b) => (
      Math.hypot(a.point.x - avatar.x, a.point.y - avatar.y) -
      Math.hypot(b.point.x - avatar.x, b.point.y - avatar.y) || a.order - b.order
    ))[0]?.point || null;
}
import type { ArrayCamera, Camera } from "three";
