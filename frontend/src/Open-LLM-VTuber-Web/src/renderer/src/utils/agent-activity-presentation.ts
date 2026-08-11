import type {
  AgentActivity,
  AgentActivityTool,
} from "@/context/agent-activity-context";

const FRIENDLY_VERBS: Record<string, string> = {
  get: "Getting",
  list: "Listing",
  search: "Searching",
  find: "Finding",
  fetch: "Fetching",
  read: "Reading",
  load: "Loading",
  check: "Checking",
  create: "Creating",
  add: "Adding",
  update: "Updating",
  edit: "Editing",
  delete: "Deleting",
  remove: "Removing",
  send: "Sending",
  move: "Moving",
  open: "Opening",
  close: "Closing",
  set: "Setting",
  run: "Running",
  archive: "Archiving",
  materialize: "Materializing",
  offer: "Offering",
  dismiss: "Dismissing",
  schedule: "Scheduling",
  mark: "Marking",
  verify: "Verifying",
  summarize: "Summarizing",
  inspect: "Inspecting",
  review: "Reviewing",
  prepare: "Preparing",
  draft: "Drafting",
  save: "Saving",
  share: "Sharing",
  sync: "Syncing",
  upload: "Uploading",
  download: "Downloading",
  publish: "Publishing",
  reply: "Replying",
  respond: "Responding",
  start: "Starting",
  stop: "Stopping",
  enable: "Enabling",
  disable: "Disabling",
  rename: "Renaming",
  approve: "Approving",
  cancel: "Cancelling",
  pause: "Pausing",
  resume: "Resuming",
  monitor: "Monitoring",
  watch: "Watching",
  track: "Tracking",
  refresh: "Refreshing",
};

export function friendlyToolName(toolName: string, unknownLabel: string) {
  if (!toolName || toolName === "unknown_task") return unknownLabel;
  const words = toolName
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .trim()
    .toLowerCase()
    .split(/\s+/);
  const verb = FRIENDLY_VERBS[words[0]];
  if (verb) return `${verb} ${words.slice(1).join(" ") || "task"}`;
  return words.map((word, index) => (
    index === 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word
  )).join(" ");
}

/** Choose the single tool that best represents what the agent is doing now. */
export function selectCurrentActivityTool(tools: AgentActivityTool[]) {
  return tools.find((tool) => tool.status === "running")
    || tools.find((tool) => tool.status === "pending")
    || tools.at(-1)
    || null;
}

interface ActivityLabels {
  thinking: string;
  planning: string;
  plan: string;
  unknownTool: string;
}

export function currentActivityLabel(
  activity: AgentActivity,
  labels: ActivityLabels,
): string | null {
  if (activity.phase === "idle") return null;
  if (activity.phase === "thinking") return labels.thinking;
  if (activity.phase === "planning") return labels.planning;
  if (activity.phase === "plan") return activity.label || labels.plan;
  const tool = selectCurrentActivityTool(activity.tools);
  return `${friendlyToolName(tool?.tool_name || "", labels.unknownTool)}…`;
}
