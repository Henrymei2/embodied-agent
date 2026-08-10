import { useEffect, useRef, useState } from "react";
import { Button } from "@chakra-ui/react";
import { FiX } from "react-icons/fi";
import { useTranslation } from "react-i18next";
import * as THREE from "three";
import {
  useVrStage, VrAvatarMotionPlan, VrStageNode, VrTransientEntity,
} from "@/context/vr-stage-context";
import { useRendererMode } from "@/context/renderer-mode-context";
import { useVAD } from "@/context/vad-context";
import { useSubtitle } from "@/context/subtitle-context";
import { useAgentActivity } from "@/context/agent-activity-context";
import { useWebSocket } from "@/context/websocket-context";
import {
  initialVrScenarioMenuPage,
  nextVrScenarioMenuPage,
  useVrScenario,
  VrScenarioMenuAction,
  VrScenarioMenuPage,
} from "@/context/vr-scenario-context";
import { wsService } from "@/services/websocket-service";
import { audioManager } from "@/utils/audio-manager";
import { currentActivityLabel } from "@/utils/agent-activity-presentation";
import {
  fitFurnitureModel,
  furnitureModelAsset,
  loadFurnitureModel,
} from "@/utils/vr-furniture-models";
import {
  furniturePanelContent,
  floorToScene,
  furniturePanelLayout,
  furniturePanelRotationY,
  furnitureCenterSceneY,
  furnitureDecalLayouts,
  headRelativeDelta,
  inputSourcesByHand,
  isViewerPositionClear,
  pressedEdge,
  isRayGrabbableNode,
  nextEmailPage,
  questLocomotionStick,
  rescaleThumbstick,
  selectVrRayHit,
  selectViewerSpawn,
  snapTurnStep,
  transientTriggerBehavior,
  updateFurniturePanelProximity,
  clampCaptionText,
  viewerForwardFromPose,
  viewerPoseCamera,
  viewerWorldPositionFromPose,
  VR_RAY_MAX_DISTANCE,
  loadVrSelectionHighlight,
} from "@/utils/vr-helpers";

const SCALE = 0.01;
const MOVE_SPEED = 1.5;
const DEAD_ZONE = 0.2;
const SNAP_THRESHOLD = 0.7;
const SNAP_REARM = 0.3;
const SNAP_RADIANS = THREE.MathUtils.degToRad(30);

interface AvatarMotionRuntime extends VrAvatarMotionPlan {
  startedAt: number;
  fromScene: THREE.Vector3;
  toScene: THREE.Vector3;
  fromQuaternion: THREE.Quaternion;
  walkQuaternion: THREE.Quaternion;
  finalQuaternion: THREE.Quaternion;
}

interface VrRuntime {
  scene: THREE.Scene;
  renderer: THREE.WebGLRenderer;
  camera: THREE.PerspectiveCamera;
  rig: THREE.Group;
  furniture: THREE.Group;
  transientRoot: THREE.Group;
  avatar: THREE.Group | null;
  panels: Map<string, THREE.Mesh>;
  raycaster: THREE.Raycaster;
  controllers: THREE.XRTargetRaySpace[];
  hud: THREE.Mesh;
  hudTexture: THREE.Texture;
  systemHud: THREE.Mesh;
  systemHudTexture: THREE.Texture;
  activityBubble: THREE.Mesh;
  activityBubbleTexture: THREE.Texture;
  turnArmed: boolean;
  xPressed: boolean;
  lastFrame: number;
  collisionNodes: VrStageNode[];
  panelGenerations: Map<string, number>;
  dismissedPanels: Set<string>;
  textureWarnings: Set<string>;
  grabbed: { object: THREE.Object3D; entityId: string; kind: "prop" | "transient"; wasOffered: boolean } | null;
  poseSequence: number;
  eventSequence: number;
  lastPoseSentAt: number;
  lastPose: { x: number; y: number; yaw: number } | null;
  hoveredSelectable: THREE.Object3D | null;
  scenarioMenu: THREE.Group;
  scenarioPanel: THREE.Mesh;
  scenarioMenuOpen: boolean;
  scenarioPage: VrScenarioMenuPage;
  aPressed: boolean;
  renderScenarioMenu: () => void;
}

interface PanelAction {
  collection: "plans" | "skills";
  id: string;
  decision: "approved" | "declined";
  nodeId: string;
}

const FURNITURE_PANEL_REFRESH_MS = 3000;
const INTERACTIVE_FURNITURE_PROVIDERS = new Set(["plan_status", "skill_status"]);

const providerRoutes: Record<string, string> = {
  todo_list: "/api/furniture/todo",
  timer_status: "/api/furniture/timers",
  google_gmail_unread: "/api/integrations/google/gmail/unread-status",
  bookshelf: "/api/furniture/bookshelf",
  reading_status: "/api/furniture/reading",
  plan_status: "/api/furniture/plans",
  skill_status: "/api/furniture/skills",
  drawing_board_image: "/api/furniture/drawing-board",
  google_calendar_status: "/api/integrations/google/status",
};

function documentToScene(stage: { room: { width: number; depth: number } }, x: number, y: number) {
  const position = floorToScene(stage.room, { x, y });
  return new THREE.Vector3(position.x, 0, position.z);
}

function disposeObject(root: THREE.Object3D) {
  root.traverse((object) => {
    if (!(object instanceof THREE.Mesh || object instanceof THREE.Line)) return;
    object.geometry.dispose();
    const materials = Array.isArray(object.material) ? object.material : [object.material];
    materials.forEach((material) => {
      Object.values(material).forEach((value) => {
        if (value instanceof THREE.Texture) value.dispose();
      });
      material.dispose();
    });
  });
}

function ancestorData<T>(object: THREE.Object3D, key: string): T | undefined {
  let current: THREE.Object3D | null = object;
  while (current) {
    if (current.userData[key] !== undefined) return current.userData[key] as T;
    current = current.parent;
  }
  return undefined;
}

function ancestorWithData(object: THREE.Object3D, key: string): THREE.Object3D | null {
  let current: THREE.Object3D | null = object;
  while (current) {
    if (current.userData[key] !== undefined) return current;
    current = current.parent;
  }
  return null;
}

function facingQuaternion(from: THREE.Vector3, to: THREE.Vector3) {
  if (Math.hypot(to.x - from.x, to.z - from.z) < 0.0001) return new THREE.Quaternion();
  const helper = new THREE.Object3D();
  helper.position.copy(from);
  helper.lookAt(to.x, from.y, to.z);
  return helper.quaternion.clone();
}

const smoothstep = (value: number) => value * value * (3 - 2 * value);

function roundedRect(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
) {
  context.beginPath();
  context.roundRect(x, y, width, height, radius);
  context.closePath();
}

function canvasTexture(lines: string[], width = 768, height = 256, maxLines = 5) {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d")!;
  const background = context.createLinearGradient(0, 0, width, height);
  background.addColorStop(0, "rgba(15, 23, 42, 0.96)");
  background.addColorStop(1, "rgba(7, 12, 24, 0.96)");
  roundedRect(context, 4, 4, width - 8, height - 8, 28);
  context.fillStyle = background;
  context.fill();
  context.strokeStyle = "rgba(199, 210, 254, 0.42)";
  context.lineWidth = 3;
  context.stroke();
  context.fillStyle = "#f8fafc";
  context.font = "600 34px system-ui, sans-serif";
  lines.slice(0, maxLines).forEach((line, index) => {
    const clipped = line.length > 48 ? `${line.slice(0, 47)}…` : line;
    context.fillText(clipped, 30, 52 + index * 43);
  });
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.minFilter = THREE.LinearMipmapLinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.needsUpdate = true;
  return texture;
}

function scenarioMenuTexture(lines: string[]) {
  const canvas = document.createElement("canvas");
  canvas.width = 1280;
  canvas.height = 920;
  const context = canvas.getContext("2d")!;
  const gradient = context.createLinearGradient(0, 0, canvas.width, canvas.height);
  gradient.addColorStop(0, "rgba(15, 23, 42, 0.98)");
  gradient.addColorStop(1, "rgba(6, 10, 22, 0.98)");
  roundedRect(context, 8, 8, canvas.width - 16, canvas.height - 16, 54);
  context.fillStyle = gradient;
  context.fill();
  context.strokeStyle = "rgba(103, 232, 249, 0.58)";
  context.lineWidth = 5;
  context.stroke();
  lines.slice(0, 14).forEach((line, index) => {
    context.fillStyle = index === 0 ? "#f8fafc" : index === 1 ? "#fbbf24" : "#dbeafe";
    context.font = index === 0 ? "700 54px system-ui, sans-serif" : "500 35px system-ui, sans-serif";
    context.fillText(line.length > 68 ? `${line.slice(0, 67)}…` : line, 58, 82 + index * 57);
  });
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.needsUpdate = true;
  return texture;
}

function captionTexture(caption: string, status: string) {
  const width = 1536;
  const height = 384;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d")!;
  const background = context.createLinearGradient(0, 0, width, height);
  background.addColorStop(0, "rgba(12, 18, 32, 0.9)");
  background.addColorStop(1, "rgba(7, 10, 20, 0.84)");
  roundedRect(context, 8, 8, width - 16, height - 16, 64);
  context.fillStyle = background;
  context.fill();
  context.strokeStyle = "rgba(255, 255, 255, 0.17)";
  context.lineWidth = 4;
  context.stroke();

  context.fillStyle = "rgba(199, 210, 254, 0.92)";
  context.font = "650 30px system-ui, sans-serif";
  context.letterSpacing = "2px";
  context.fillText(status.toUpperCase(), 72, 68);
  context.letterSpacing = "0px";
  context.fillStyle = "#f8fafc";
  context.font = "600 54px system-ui, sans-serif";
  clampCaptionText(caption, 48, 3).forEach((line, index) => {
    context.fillText(line, 72, 146 + index * 70);
  });

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.minFilter = THREE.LinearMipmapLinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.needsUpdate = true;
  return texture;
}

function activityBubbleTexture(label: string) {
  const canvas = document.createElement("canvas");
  canvas.width = 1280;
  canvas.height = 256;
  const context = canvas.getContext("2d")!;
  const background = context.createLinearGradient(0, 0, canvas.width, canvas.height);
  background.addColorStop(0, "rgba(15, 23, 42, 0.96)");
  background.addColorStop(1, "rgba(7, 12, 24, 0.94)");
  roundedRect(context, 8, 8, canvas.width - 16, canvas.height - 16, 112);
  context.fillStyle = background;
  context.fill();
  context.strokeStyle = "rgba(199, 210, 254, 0.55)";
  context.lineWidth = 5;
  context.stroke();
  context.beginPath();
  context.arc(82, canvas.height / 2, 15, 0, Math.PI * 2);
  context.fillStyle = "#a5b4fc";
  context.shadowColor = "rgba(129, 140, 248, 0.9)";
  context.shadowBlur = 24;
  context.fill();
  context.shadowBlur = 0;
  context.fillStyle = "#f8fafc";
  context.font = "650 58px system-ui, sans-serif";
  const maxTextWidth = canvas.width - 170;
  let visibleLabel = label;
  while (visibleLabel.length > 1 && context.measureText(visibleLabel).width > maxTextWidth) {
    visibleLabel = visibleLabel.slice(0, -1);
  }
  if (visibleLabel !== label) visibleLabel = `${visibleLabel.trimEnd()}…`;
  context.fillText(visibleLabel, 128, 150);

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.minFilter = THREE.LinearMipmapLinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.needsUpdate = true;
  return texture;
}

function createSpatialAvatar(node: VrStageNode) {
  const avatar = new THREE.Group();
  avatar.name = "spatial-companion";
  const shell = new THREE.MeshStandardMaterial({
    color: new THREE.Color(node.color), roughness: 0.28, metalness: 0.16,
  });
  const porcelain = new THREE.MeshStandardMaterial({
    color: 0xf1f5ff, roughness: 0.22, metalness: 0.08,
  });
  const darkGlass = new THREE.MeshStandardMaterial({
    color: 0x0b1020, roughness: 0.12, metalness: 0.5,
  });
  const glow = new THREE.MeshStandardMaterial({
    color: 0xc7d2fe, emissive: 0x818cf8, emissiveIntensity: 2.1, roughness: 0.18,
  });

  const shadow = new THREE.Mesh(
    new THREE.CircleGeometry(0.42, 48),
    new THREE.MeshBasicMaterial({ color: 0x05070d, transparent: true, opacity: 0.23, depthWrite: false }),
  );
  shadow.rotation.x = -Math.PI / 2;
  shadow.position.y = 0.006;
  avatar.add(shadow);

  const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.25, 0.43, 8, 20), shell);
  body.position.y = 0.72;
  body.castShadow = true;
  avatar.add(body);
  const chest = new THREE.Mesh(new THREE.CircleGeometry(0.075, 32), glow);
  chest.position.set(0, 0.77, 0.255);
  avatar.add(chest);

  const head = new THREE.Mesh(new THREE.SphereGeometry(0.34, 40, 24), porcelain);
  head.scale.set(1.04, 0.9, 0.92);
  head.position.y = 1.25;
  head.castShadow = true;
  avatar.add(head);
  // A shallow spherical visor follows the head instead of floating as a flat
  // plane in front of it. The slightly larger radius prevents z-fighting while
  // preserving the same silhouette and material treatment.
  const face = new THREE.Mesh(
    new THREE.SphereGeometry(
      0.347,
      32,
      12,
      Math.PI / 2 - 0.64,
      1.28,
      Math.PI / 2 - 0.31,
      0.62,
    ),
    darkGlass,
  );
  face.scale.copy(head.scale);
  face.position.y = 1.25;
  avatar.add(face);
  const leftEye = new THREE.Mesh(new THREE.CircleGeometry(0.035, 24), glow);
  leftEye.position.set(-0.09, 1.27, 0.307);
  avatar.add(leftEye);
  const rightEye = leftEye.clone();
  rightEye.position.x = 0.09;
  avatar.add(rightEye);

  const armGeometry = new THREE.CapsuleGeometry(0.065, 0.33, 6, 14);
  const leftArm = new THREE.Mesh(armGeometry, porcelain);
  leftArm.position.set(-0.34, 0.72, 0);
  leftArm.rotation.z = -0.12;
  leftArm.castShadow = true;
  avatar.add(leftArm);
  const rightArm = leftArm.clone();
  rightArm.position.x = 0.34;
  rightArm.rotation.z = 0.12;
  avatar.add(rightArm);

  const legGeometry = new THREE.CapsuleGeometry(0.075, 0.25, 6, 14);
  const leftLeg = new THREE.Mesh(legGeometry, shell);
  leftLeg.position.set(-0.12, 0.26, 0);
  leftLeg.castShadow = true;
  avatar.add(leftLeg);
  const rightLeg = leftLeg.clone();
  rightLeg.position.x = 0.12;
  avatar.add(rightLeg);

  const halo = new THREE.Mesh(new THREE.TorusGeometry(0.43, 0.008, 8, 64), glow);
  halo.position.set(0, 1.25, -0.13);
  avatar.add(halo);
  const presenceLight = new THREE.PointLight(0x818cf8, 0.65, 2.4, 2);
  presenceLight.position.set(0, 1.05, 0.35);
  avatar.add(presenceLight);
  avatar.userData.visuals = { body, head, leftArm, rightArm, chest, halo };
  return avatar;
}

export default function VrStage() {
  const { t } = useTranslation();
  const hostRef = useRef<HTMLDivElement | null>(null);
  const runtimeRef = useRef<VrRuntime | null>(null);
  const { stage, motionPlan, transients, propStates, setUserPose, updateTransient, removeTransient, updatePropState } = useVrStage();
  const transientRef = useRef(transients);
  const stageRef = useRef(stage);
  const { xrSession, leaveVr } = useRendererMode();
  const { micOn, startMic, stopMic } = useVAD();
  const micRef = useRef(micOn);
  const { subtitleText, showSubtitle } = useSubtitle();
  const { activity } = useAgentActivity();
  const { baseUrl, wsState } = useWebSocket();
  const scenarioState = useVrScenario();
  const scenarioStateRef = useRef(scenarioState);
  const baseUrlRef = useRef(baseUrl);
  const startMicRef = useRef(startMic);
  const stopMicRef = useRef(stopMic);
  const micStatusTimerRef = useRef<number | null>(null);
  const warningTimerRef = useRef<number | null>(null);
  const audioWarningTimerRef = useRef<number | null>(null);
  const playbackHideTimerRef = useRef<number | null>(null);
  const [micStatus, setMicStatus] = useState<string | null>(null);
  const [vrWarning, setVrWarning] = useState<string | null>(null);
  const [audioWarning, setAudioWarning] = useState<string | null>(null);
  const [audioPlaying, setAudioPlaying] = useState(false);
  const initialMicOnRef = useRef(micOn);
  const selectionHighlightEnabled = loadVrSelectionHighlight();

  useEffect(() => { stageRef.current = stage; }, [stage]);
  useEffect(() => { transientRef.current = transients; }, [transients]);
  useEffect(() => { micRef.current = micOn; }, [micOn]);
  useEffect(() => { baseUrlRef.current = baseUrl; }, [baseUrl]);
  useEffect(() => { startMicRef.current = startMic; }, [startMic]);
  useEffect(() => { stopMicRef.current = stopMic; }, [stopMic]);
  useEffect(() => {
    scenarioStateRef.current = scenarioState;
    runtimeRef.current?.renderScenarioMenu();
  }, [scenarioState]);

  useEffect(() => {
    if (!xrSession) return undefined;
    scenarioState.refresh();
    const interval = window.setInterval(() => scenarioStateRef.current.refresh(), 2000);
    return () => window.clearInterval(interval);
  }, [xrSession, scenarioState.refresh]);

  useEffect(() => {
    if (xrSession && initialMicOnRef.current) void startMicRef.current();
  }, [xrSession]);

  useEffect(() => () => {
    if (micStatusTimerRef.current !== null) window.clearTimeout(micStatusTimerRef.current);
    if (warningTimerRef.current !== null) window.clearTimeout(warningTimerRef.current);
    if (audioWarningTimerRef.current !== null) window.clearTimeout(audioWarningTimerRef.current);
    if (playbackHideTimerRef.current !== null) window.clearTimeout(playbackHideTimerRef.current);
  }, []);

  useEffect(() => audioManager.subscribe((state) => {
    if (state.state === "playing") {
      if (playbackHideTimerRef.current !== null) {
        window.clearTimeout(playbackHideTimerRef.current);
        playbackHideTimerRef.current = null;
      }
      if (audioWarningTimerRef.current !== null) {
        window.clearTimeout(audioWarningTimerRef.current);
        audioWarningTimerRef.current = null;
      }
      setAudioWarning(null);
      setAudioPlaying(true);
      return;
    }
    if (state.state === "error") {
      if (playbackHideTimerRef.current !== null) window.clearTimeout(playbackHideTimerRef.current);
      playbackHideTimerRef.current = null;
      setAudioPlaying(false);
      setAudioWarning(state.message);
      if (audioWarningTimerRef.current !== null) window.clearTimeout(audioWarningTimerRef.current);
      audioWarningTimerRef.current = window.setTimeout(() => {
        setAudioWarning(null);
        audioWarningTimerRef.current = null;
      }, 3000);
      return;
    }
    if (playbackHideTimerRef.current !== null) window.clearTimeout(playbackHideTimerRef.current);
    playbackHideTimerRef.current = window.setTimeout(() => {
      setAudioPlaying(false);
      playbackHideTimerRef.current = null;
    }, 120);
  }), []);

  useEffect(() => {
    if (!hostRef.current || !xrSession) return undefined;
    const host = hostRef.current;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#0c1120");
    scene.fog = new THREE.FogExp2("#111827", 0.025);
    const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.05, 50);
    const rig = new THREE.Group();
    rig.add(camera);
    scene.add(rig);
    scene.add(new THREE.HemisphereLight(0xeef2ff, 0x51422e, 1.55));
    const directional = new THREE.DirectionalLight(0xfff4df, 2.25);
    directional.position.set(-2.5, 5.5, 2.8);
    directional.castShadow = true;
    directional.shadow.mapSize.set(1024, 1024);
    directional.shadow.camera.near = 0.1;
    directional.shadow.camera.far = 16;
    directional.shadow.bias = -0.0008;
    scene.add(directional);
    const roomGlow = new THREE.PointLight(0x8ea4ff, 1.2, 8, 2);
    roomGlow.position.set(2.8, 2.3, -1.8);
    scene.add(roomGlow);
    const furniture = new THREE.Group();
    scene.add(furniture);
    const transientRoot = new THREE.Group();
    scene.add(transientRoot);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.08;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.xr.enabled = true;
    renderer.xr.setReferenceSpaceType("local-floor");
    host.appendChild(renderer.domElement);

    const controllers = [renderer.xr.getController(0), renderer.xr.getController(1)];
    controllers.forEach((controller) => {
      const points = [new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 0, -VR_RAY_MAX_DISTANCE)];
      const ray = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(points),
        new THREE.LineBasicMaterial({ color: 0xc7d2fe, transparent: true, opacity: 0.58 }),
      );
      ray.visible = false;
      controller.add(ray);
      const reticle = new THREE.Mesh(
        new THREE.RingGeometry(0.009, 0.014, 24),
        new THREE.MeshBasicMaterial({ color: 0xe0e7ff, transparent: true, opacity: 0.82, side: THREE.DoubleSide }),
      );
      reticle.position.z = -VR_RAY_MAX_DISTANCE;
      reticle.visible = false;
      controller.add(reticle);
      controller.userData.ray = ray;
      controller.userData.reticle = reticle;
      // Target-ray poses are reported in XR reference-space coordinates. Keep
      // them under the same locomotion rig as the headset so walking and snap
      // turns transform the head and hands together.
      rig.add(controller);
    });

    const hudTexture = captionTexture("Preparing your companion space", "Connecting");
    const hud = new THREE.Mesh(
      new THREE.PlaneGeometry(1.12, 0.28),
      new THREE.MeshBasicMaterial({
        map: hudTexture, transparent: true, depthTest: false, depthWrite: false,
      }),
    );
    hud.position.set(0, -0.48, -1.62);
    hud.renderOrder = 100;
    hud.visible = false;
    hud.raycast = () => undefined;
    camera.add(hud);

    const systemHudTexture = captionTexture("Preparing your companion space", "Connecting");
    const systemHud = new THREE.Mesh(
      new THREE.PlaneGeometry(0.9, 0.225),
      new THREE.MeshBasicMaterial({
        map: systemHudTexture, transparent: true, depthTest: false, depthWrite: false,
      }),
    );
    systemHud.position.set(0, 0.42, -1.65);
    systemHud.renderOrder = 110;
    systemHud.raycast = () => undefined;
    camera.add(systemHud);

    const initialActivityBubbleTexture = activityBubbleTexture("");
    const activityBubble = new THREE.Mesh(
      new THREE.PlaneGeometry(0.92, 0.184),
      new THREE.MeshBasicMaterial({
        map: initialActivityBubbleTexture,
        transparent: true,
        depthTest: false,
        depthWrite: false,
        side: THREE.DoubleSide,
      }),
    );
    activityBubble.renderOrder = 90;
    activityBubble.visible = false;
    activityBubble.raycast = () => undefined;
    scene.add(activityBubble);

    const scenarioMenu = new THREE.Group();
    scenarioMenu.position.set(0, 0, -1.55);
    scenarioMenu.visible = false;
    camera.add(scenarioMenu);
    const scenarioPanel = new THREE.Mesh(
      new THREE.PlaneGeometry(1.5, 1.08),
      new THREE.MeshBasicMaterial({
        map: scenarioMenuTexture(["VR test scenarios", "Loading…"]),
        transparent: true,
        depthTest: false,
      }),
    );
    scenarioPanel.renderOrder = 200;
    scenarioPanel.userData.scenarioPanel = true;
    scenarioMenu.add(scenarioPanel);

    const runtime: VrRuntime = {
      scene, renderer, camera, rig, furniture, transientRoot, avatar: null,
      panels: new Map(),
      raycaster: new THREE.Raycaster(undefined, undefined, 0, VR_RAY_MAX_DISTANCE), controllers, hud, hudTexture,
      systemHud, systemHudTexture,
      activityBubble, activityBubbleTexture: initialActivityBubbleTexture,
      turnArmed: true, xPressed: false, lastFrame: performance.now(),
      collisionNodes: [],
      panelGenerations: new Map(), dismissedPanels: new Set(),
      textureWarnings: new Set<string>(),
      grabbed: null, poseSequence: 0, eventSequence: 0, lastPoseSentAt: 0, lastPose: null,
      hoveredSelectable: null,
      scenarioMenu, scenarioPanel, scenarioMenuOpen: false, scenarioPage: "catalog",
      aPressed: false, renderScenarioMenu: () => undefined,
    };
    runtimeRef.current = runtime;

    const setPanelLines = (panel: THREE.Mesh, lines: string[]) => {
      const material = panel.material as THREE.MeshBasicMaterial;
      const previous = material.map;
      const layout = furniturePanelLayout(panel.userData.provider as string | undefined);
      material.map = canvasTexture(
        lines,
        layout.textureWidth,
        layout.textureHeight,
        layout.maxLines,
      );
      material.needsUpdate = true;
      previous?.dispose();
    };

    const closePanel = (nodeId: string, dismiss = false) => {
      runtime.panelGenerations.set(
        nodeId,
        (runtime.panelGenerations.get(nodeId) || 0) + 1,
      );
      if (dismiss) runtime.dismissedPanels.add(nodeId);
      const panel = runtime.panels.get(nodeId);
      if (!panel) return;
      scene.remove(panel);
      disposeObject(panel);
      runtime.panels.delete(nodeId);
    };

    const closeAllPanels = () => {
      [...runtime.panels.keys()].forEach((nodeId) => closePanel(nodeId));
    };

    const addScenarioButton = (label: string, action: string, x: number, color: number) => {
      const button = new THREE.Mesh(
        new THREE.PlaneGeometry(0.58, 0.15),
        new THREE.MeshBasicMaterial({
          map: canvasTexture([label], 384, 100), transparent: true, depthTest: false, color,
        }),
      );
      button.position.set(x, -0.43, 0.018);
      button.renderOrder = 202;
      button.userData.scenarioAction = action;
      scenarioMenu.add(button);
    };

    runtime.renderScenarioMenu = () => {
      while (scenarioMenu.children.length > 1) {
        const child = scenarioMenu.children[scenarioMenu.children.length - 1];
        scenarioMenu.remove(child);
        disposeObject(child);
      }
      const state = scenarioStateRef.current;
      const definition = state.scenarios[0];
      const run = state.active_run;
      let lines: string[];
      if (run && runtime.scenarioPage === "status") {
        lines = [
          definition?.title || "VR test scenario",
          "SIMULATED · LIVE STATUS, NOT A SCORE",
          `Phase: ${run.phase.replace(/_/g, " ")}`,
          run.progress_summary || "Waiting for an update…",
          ...(run.error ? [`Error: ${run.error}`] : []),
          "A closes this menu. Right trigger selects a control.",
        ];
        if (["preparing", "waiting_event", "planning", "running", "awaiting_approval", "cancelling"].includes(run.phase)) {
          addScenarioButton("Scenarios", "back", -0.34, 0xffffff);
          addScenarioButton(run.phase === "cancelling" ? "Stopping…" : "Cancel", "cancel", 0.34, 0xffb4a2);
        } else {
          addScenarioButton("Scenarios", "back", 0, 0xffffff);
        }
      } else if (runtime.scenarioPage === "confirm" && definition) {
        lines = [
          definition.title,
          "SIMULATED · RESET CONFIRMATION",
          definition.reset_notice,
          "Real Google data will not be read or changed.",
          "Start the scenario now?",
        ];
        addScenarioButton("Back", "back", -0.34, 0xffffff);
        addScenarioButton("Start", "start", 0.34, 0xa7f3d0);
      } else if (definition) {
        const active = run && ["preparing", "waiting_event", "planning", "running", "awaiting_approval", "cancelling"].includes(run.phase);
        lines = [
          "VR test scenarios",
          definition.title,
          "SIMULATED",
          definition.summary,
          ...definition.sequence.slice(0, 4).map((item, index) => `${index + 1}. ${item}`),
          `Shows: ${definition.demonstrates.slice(0, 3).join(" · ")}`,
          ...(run ? [`Last run: ${run.phase.replace(/_/g, " ")}`] : []),
          active
            ? "A scenario is running. View its status or cancel it there."
            : "Select Details to review the reset and start.",
        ];
        if (active) {
          addScenarioButton("View status", "status", 0, 0xa5f3fc);
        } else if (run) {
          addScenarioButton("Details", "details", -0.34, 0xa5f3fc);
          addScenarioButton("Last result", "status", 0.34, 0xffffff);
        } else {
          addScenarioButton("Details", "details", 0, 0xa5f3fc);
        }
      } else {
        lines = ["VR test scenarios", "Loading scenario catalogue…"];
      }
      if (state.error) lines.splice(2, 0, `Error: ${state.error}`);
      const material = scenarioPanel.material as THREE.MeshBasicMaterial;
      material.map?.dispose();
      material.map = scenarioMenuTexture(lines);
      material.needsUpdate = true;
    };
    runtime.renderScenarioMenu();

    const addCloseControl = (
      parent: THREE.Object3D,
      position: THREE.Vector3,
      userData: Record<string, unknown>,
    ) => {
      const visible = new THREE.Mesh(
        new THREE.PlaneGeometry(0.14, 0.14),
        new THREE.MeshBasicMaterial({ map: canvasTexture(["×"], 128, 128), transparent: true }),
      );
      visible.position.copy(position);
      Object.assign(visible.userData, userData);
      parent.add(visible);
      const hitTarget = new THREE.Mesh(
        new THREE.PlaneGeometry(0.26, 0.24),
        new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false }),
      );
      hitTarget.position.copy(position).add(new THREE.Vector3(0, 0, 0.004));
      Object.assign(hitTarget.userData, userData);
      parent.add(hitTarget);
    };

    const interactiveRoots = () => [
      ...(runtime.scenarioMenuOpen ? [runtime.scenarioMenu] : []),
      ...runtime.panels.values(),
      furniture,
      transientRoot,
      ...(runtime.avatar ? [runtime.avatar] : []),
    ];

    const rayHitFromController = (controller: THREE.Object3D) => {
      const origin = new THREE.Vector3();
      const direction = new THREE.Vector3(0, 0, -1);
      controller.getWorldPosition(origin);
      direction.applyQuaternion(controller.getWorldQuaternion(new THREE.Quaternion()));
      runtime.raycaster.set(origin, direction);
      const hits = runtime.raycaster.intersectObjects(interactiveRoots(), true);
      return selectVrRayHit(
        hits,
        origin,
        direction,
      );
    };

    const setHoveredSelectable = (mesh: THREE.Object3D | null) => {
      if (runtime.hoveredSelectable === mesh) return;
      const setOutline = (target: THREE.Object3D | null, active: boolean) => {
        const outline = target?.userData.selectionOutline as THREE.LineSegments | undefined;
        if (!outline) return;
        const material = outline.material as THREE.LineBasicMaterial;
        material.color.set(active ? 0x67e8f9 : 0xffffff);
        material.opacity = active ? 0.95 : 0.1;
      };
      setOutline(runtime.hoveredSelectable, false);
      runtime.hoveredSelectable = mesh;
      setOutline(mesh, selectionHighlightEnabled);
    };

    const removePanelActions = (panel: THREE.Mesh) => {
      [...panel.children].forEach((child) => {
        if (child.userData.panelAction) {
          panel.remove(child);
          disposeObject(child);
        }
      });
    };

    const renderPanelContent = (
      panel: THREE.Mesh,
      liveData: Record<string, unknown> | null,
      failed: boolean,
    ) => {
      const node = panel.userData.node as VrStageNode;
      const provider = panel.userData.provider as string | undefined;
      const windowConfig: NonNullable<VrStageNode["uiWindow"]> = node.uiWindow || {
        title: node.label || node.id,
        body: "No additional information is available for this furniture.",
      };
      const content = furniturePanelContent(provider, liveData);
      const lines = [
        windowConfig.title,
        windowConfig.body || "",
        ...content.lines,
      ];
      if (provider === "google_calendar_status" && liveData?.connected !== true) {
        lines.splice(2, 0, "Exit VR to connect Google");
      }
      if (failed) lines.splice(2, 0, "Live data unavailable");
      lines.push("Close hides this panel until you leave");
      setPanelLines(panel, lines);
      removePanelActions(panel);
      if (content.decision) {
        const { collection, id } = content.decision;
        const layout = furniturePanelLayout(provider);
        (["approved", "declined"] as const).forEach((decision, index) => {
          const button = new THREE.Mesh(
            new THREE.PlaneGeometry(0.55, 0.15),
            new THREE.MeshBasicMaterial({
              map: canvasTexture([decision === "approved" ? "Approve" : "Decline"], 320, 96),
              transparent: true,
            }),
          );
          button.position.set(index === 0 ? -0.32 : 0.32, layout.actionY, 0.01);
          button.userData.panelAction = {
            collection,
            id,
            decision,
            nodeId: node.id,
          } satisfies PanelAction;
          panel.add(button);
        });
      }
    };

    const loadPanelContent = async (
      nodeId: string,
      panel: THREE.Mesh,
      generation: number,
      keepOnFailure: boolean,
    ) => {
      const provider = panel.userData.provider as string | undefined;
      const route = provider ? providerRoutes[provider] : undefined;
      let liveData: Record<string, unknown> | null = null;
      let failed = false;
      if (route) {
        try {
          const response = await fetch(`${baseUrlRef.current}${route}`, {
            headers: { Accept: "application/json" },
          });
          if (response.ok) {
            liveData = await response.json() as Record<string, unknown>;
          } else {
            failed = true;
          }
        } catch (_error) {
          failed = true;
        }
      }
      if (
        runtime.panels.get(nodeId) !== panel ||
        runtime.panelGenerations.get(nodeId) !== generation
      ) return;
      if (failed && keepOnFailure) return;
      renderPanelContent(panel, liveData, failed);
    };

    const refreshPanel = async (nodeId: string) => {
      const panel = runtime.panels.get(nodeId);
      if (!panel) return;
      const generation = (runtime.panelGenerations.get(nodeId) || 0) + 1;
      runtime.panelGenerations.set(nodeId, generation);
      await loadPanelContent(nodeId, panel, generation, true);
    };

    const openFurniturePanel = async (node: VrStageNode) => {
      if (runtime.panels.has(node.id) || runtime.dismissedPanels.has(node.id)) return;
      const windowConfig: NonNullable<VrStageNode["uiWindow"]> = node.uiWindow || {
        title: node.label || node.id,
        body: "No additional information is available for this furniture.",
      };
      const layout = furniturePanelLayout(windowConfig.provider);
      const generation = (runtime.panelGenerations.get(node.id) || 0) + 1;
      runtime.panelGenerations.set(node.id, generation);
      const panel = new THREE.Mesh(
        new THREE.PlaneGeometry(layout.width, layout.height),
        new THREE.MeshBasicMaterial({
          map: canvasTexture(
            [windowConfig.title, windowConfig.body || "", "Loading…"],
            layout.textureWidth,
            layout.textureHeight,
            layout.maxLines,
          ),
          transparent: true,
          side: THREE.DoubleSide,
        }),
      );
      const position = documentToScene(stageRef.current!, node.x, node.y);
      panel.position.set(
        position.x,
        Math.min(2.25, (node.elevation + node.height / 2 + 45) * SCALE),
        position.z,
      );
      panel.rotation.y = furniturePanelRotationY(
        node.y,
        stageRef.current!.room.depth,
      );
      panel.userData.panel = true;
      panel.userData.node = node;
      panel.userData.nodeId = node.id;
      panel.userData.provider = windowConfig.provider;
      addCloseControl(
        panel,
        new THREE.Vector3(
          layout.closePosition.x,
          layout.closePosition.y,
          layout.closePosition.z,
        ),
        { panelClose: node.id },
      );
      scene.add(panel);
      runtime.panels.set(node.id, panel);
      await loadPanelContent(node.id, panel, generation, false);
    };

    const activatePanelAction = async (action: PanelAction) => {
      try {
        const response = await fetch(`${baseUrlRef.current}/api/furniture/${action.collection}/${encodeURIComponent(action.id)}/decision`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ decision: action.decision }),
        });
        if (!response.ok) throw new Error("Decision was rejected");
        const panel = runtime.panels.get(action.nodeId);
        if (!panel) return;
        // Optimistic feedback: drop the buttons and show the decision right
        // away, then let the authoritative refresh replace this text.
        removePanelActions(panel);
        const node = panel.userData.node as VrStageNode;
        const noun = action.collection === "plans" ? "Plan" : "Skill";
        const outcome = action.decision === "approved" ? "approved" : "declined";
        setPanelLines(panel, [
          node.uiWindow?.title || node.label || node.id,
          node.uiWindow?.body || "",
          `${noun} ${outcome} — refreshing…`,
          "Close hides this panel until you leave",
        ]);
        void refreshPanel(action.nodeId);
      } catch (_error) {
        // The furniture panel remains open so the user can retry.
      }
    };

    const handleSelect = (event: THREE.Event & { target: THREE.Object3D }) => {
      const controller = event.target;
      if (controller.userData.handedness !== "right") return;
      const hit = rayHitFromController(controller);
      if (!hit) return;
      const scenarioAction = hit.object.userData.scenarioAction as VrScenarioMenuAction | undefined;
      if (scenarioAction) {
        runtime.scenarioPage = nextVrScenarioMenuPage(runtime.scenarioPage, scenarioAction);
        if (scenarioAction === "start") {
          const definition = scenarioStateRef.current.scenarios[0];
          if (definition) scenarioStateRef.current.start(definition.id);
        }
        if (scenarioAction === "cancel") {
          const run = scenarioStateRef.current.active_run;
          if (run) scenarioStateRef.current.cancel(run.run_id);
        }
        runtime.renderScenarioMenu();
        return;
      }
      if (runtime.scenarioMenuOpen) return;
      const panelAction = hit.object.userData.panelAction as PanelAction | undefined;
      if (panelAction) {
        void activatePanelAction(panelAction);
        return;
      }
      const panelClose = hit.object.userData.panelClose as string | undefined;
      if (panelClose) {
        closePanel(panelClose, true);
        return;
      }
      const transientClose = hit.object.userData.transientClose as string | undefined;
      if (transientClose) {
        if (runtime.grabbed?.entityId === transientClose) runtime.grabbed = null;
        removeTransient(transientClose);
        emitPhysicalEvent({ kind: "entity_dismissed", entity_id: transientClose });
        return;
      }
      const transientHit = ancestorData<VrTransientEntity>(hit.object, "transient");
      if (transientHit && transientTriggerBehavior(transientHit.kind, transientHit.body) === "page") {
        const page = nextEmailPage(transientHit.page || 0, transientHit.body!.length);
        updateTransient({ ...transientHit, page });
        return;
      }
    };
    const handleConnected = (event: THREE.Event & { target: THREE.Object3D; data: XRInputSource }) => {
      event.target.userData.handedness = event.data.handedness;
      event.target.children.forEach((child) => { child.visible = event.data.handedness === "right"; });
    };
    const handleDisconnected = (event: THREE.Event & { target: THREE.Object3D }) => {
      event.target.userData.handedness = undefined;
      event.target.children.forEach((child) => { child.visible = false; });
    };
    const emitPhysicalEvent = (event: Record<string, unknown>) => {
      runtime.eventSequence += 1;
      wsService.sendMessage({ type: "vr-physical-events", sequence: runtime.eventSequence, events: [event] });
    };
    const handleSqueezeStart = (event: THREE.Event & { target: THREE.Object3D }) => {
      const controller = event.target;
      if (controller.userData.handedness !== "right" || runtime.grabbed) return;
      const hand = new THREE.Vector3();
      controller.getWorldPosition(hand);
      const candidates: Array<{ object: THREE.Object3D; entityId: string; kind: "prop" | "transient"; wasOffered: boolean; distance: number }> = [];
      const rayHit = rayHitFromController(controller);
      const rayTransientObject = rayHit ? ancestorWithData(rayHit.object, "transient") : null;
      const rayTransient = rayTransientObject?.userData.transient as VrTransientEntity | undefined;
      if (rayTransientObject && rayTransient) {
        controller.attach(rayTransientObject);
        rayTransientObject.position.set(0, 0, -0.35);
        rayTransientObject.quaternion.identity();
        runtime.grabbed = {
          object: rayTransientObject,
          entityId: rayTransient.id,
          kind: "transient",
          wasOffered: rayTransient.state === "offered",
        };
        return;
      }
      const rayPropObject = rayHit ? ancestorWithData(rayHit.object, "selectionOutline") : null;
      const rayProp = rayPropObject?.userData.node as VrStageNode | undefined;
      if (rayPropObject && isRayGrabbableNode(rayProp)) {
        const wasOffered = rayPropObject.userData.offeredProp === true;
        controller.attach(rayPropObject);
        rayPropObject.position.set(0, 0, -0.35);
        rayPropObject.quaternion.identity();
        runtime.grabbed = {
          object: rayPropObject,
          entityId: rayProp.id,
          kind: "prop",
          wasOffered,
        };
        emitPhysicalEvent({ kind: "prop_grabbed", entity_id: rayProp.id });
        return;
      }
      runtime.scene.traverse((object) => {
        const node = object.userData.node as VrStageNode | undefined;
        if (!node || node.type !== "prop" || node.interaction?.grabbable !== true || (object.parent !== runtime.furniture && object.parent !== runtime.avatar)) return;
        const position = new THREE.Vector3(); object.getWorldPosition(position);
        candidates.push({ object, entityId: node.id, kind: "prop", wasOffered: object.userData.offeredProp === true, distance: position.distanceTo(hand) });
      });
      runtime.scene.traverse((object) => {
        const entity = object.userData.transient as VrTransientEntity | undefined;
        if (!entity) return;
        const position = new THREE.Vector3(); object.getWorldPosition(position);
        candidates.push({ object, entityId: entity.id, kind: "transient", wasOffered: entity.state === "offered", distance: position.distanceTo(hand) });
      });
      const candidate = candidates.filter((item) => item.distance <= 0.2).sort((a, b) => a.distance - b.distance)[0];
      if (!candidate) return;
      controller.attach(candidate.object);
      runtime.grabbed = candidate;
      if (candidate.kind === "prop") emitPhysicalEvent({ kind: "prop_grabbed", entity_id: candidate.entityId });
    };
    const handleSqueezeEnd = (event: THREE.Event & { target: THREE.Object3D }) => {
      const grabbed = runtime.grabbed;
      if (!grabbed) return;
      const object = grabbed.object;
      runtime.scene.attach(object);
      const currentStage = stageRef.current;
      if (!currentStage) return;
      const position = new THREE.Vector3(); object.getWorldPosition(position);
      const floor = { x: position.x / SCALE + currentStage.room.width / 2, y: currentStage.room.depth / 2 - position.z / SCALE };
      const yaw = THREE.MathUtils.radToDeg(new THREE.Euler().setFromQuaternion(object.getWorldQuaternion(new THREE.Quaternion()), "YXZ").y);
      if (grabbed.kind === "prop") {
        updatePropState(grabbed.entityId, null);
        emitPhysicalEvent({ kind: "prop_relocated", entity_id: grabbed.entityId, transform: { ...floor, elevation: position.y / SCALE, yaw } });
        if (grabbed.wasOffered) emitPhysicalEvent({ kind: "offered_item_taken", entity_id: grabbed.entityId });
      } else {
        const entity = transientRef.current.find((item) => item.id === grabbed.entityId);
        if (entity) updateTransient({ ...entity, state: "world_locked", ...floor, elevation: position.y / SCALE, yaw });
        emitPhysicalEvent({ kind: grabbed.wasOffered ? "offered_item_taken" : "transient_grabbed", entity_id: grabbed.entityId });
      }
      runtime.grabbed = null;
      void event;
    };
    controllers.forEach((controller) => controller.addEventListener("select", handleSelect as never));
    controllers.forEach((controller) => controller.addEventListener("connected", handleConnected as never));
    controllers.forEach((controller) => controller.addEventListener("disconnected", handleDisconnected as never));
    controllers.forEach((controller) => controller.addEventListener("squeezestart", handleSqueezeStart as never));
    controllers.forEach((controller) => controller.addEventListener("squeezeend", handleSqueezeEnd as never));

    const renderFrame = (time: number, frame?: XRFrame) => {
      const currentStage = stageRef.current;
      const dt = Math.min(0.05, (time - runtime.lastFrame) / 1000);
      runtime.lastFrame = time;
      const inputSources = inputSourcesByHand(xrSession.inputSources);
      const left = inputSources.left?.gamepad;
      const right = inputSources.right?.gamepad;
      const referenceSpace = renderer.xr.getReferenceSpace();
      const liveViewerPose = frame && referenceSpace
        ? frame.getViewerPose(referenceSpace)
        : null;
      if (!runtime.scenarioMenuOpen && left && left.axes.length >= 4 && currentStage) {
        const rawAxes = questLocomotionStick(left.axes[2] || 0, left.axes[3] || 0);
        const axes = rescaleThumbstick(rawAxes.x, rawAxes.y, DEAD_ZONE);
        const forward = liveViewerPose
          ? viewerForwardFromPose(
            liveViewerPose.transform.orientation,
            rig.getWorldQuaternion(new THREE.Quaternion()),
          )
          : (() => {
            const fallback = viewerPoseCamera(renderer.xr.getCamera())
              .getWorldDirection(new THREE.Vector3());
            fallback.y = 0;
            if (fallback.lengthSq() > 0) fallback.normalize();
            return fallback;
          })();
        const delta = headRelativeDelta(axes, forward, MOVE_SPEED, dt);
        const halfWidth = currentStage.room.width * SCALE / 2 - 0.25;
        const halfDepth = currentStage.room.depth * SCALE / 2 - 0.25;
        const nextX = THREE.MathUtils.clamp(rig.position.x + delta.x, -halfWidth, halfWidth);
        const nextZ = THREE.MathUtils.clamp(rig.position.z + delta.z, -halfDepth, halfDepth);
        const sceneToFloor = (x: number, z: number) => ({
          x: x / SCALE + currentStage.room.width / 2,
          y: currentStage.room.depth / 2 - z / SCALE,
        });
        if (isViewerPositionClear(sceneToFloor(nextX, nextZ), runtime.collisionNodes, currentStage.room)) {
          rig.position.x = nextX;
          rig.position.z = nextZ;
        } else if (isViewerPositionClear(sceneToFloor(nextX, rig.position.z), runtime.collisionNodes, currentStage.room)) {
          rig.position.x = nextX;
        } else if (isViewerPositionClear(sceneToFloor(rig.position.x, nextZ), runtime.collisionNodes, currentStage.room)) {
          rig.position.z = nextZ;
        }
      }
      if (!runtime.scenarioMenuOpen && right && right.axes.length >= 3) {
        const axis = right.axes[2] || 0;
        const turn = snapTurnStep(axis, runtime.turnArmed, SNAP_THRESHOLD, SNAP_REARM, SNAP_RADIANS);
        if (turn.delta) {
          const xrCamera = viewerPoseCamera(renderer.xr.getCamera());
          const before = xrCamera.getWorldPosition(new THREE.Vector3());
          rig.rotation.y += turn.delta;
          rig.updateMatrixWorld(true);
          const after = xrCamera.getWorldPosition(new THREE.Vector3());
          rig.position.x += before.x - after.x;
          rig.position.z += before.z - after.z;
        }
        runtime.turnArmed = turn.armed;
      }
      if (left) {
        if (left.buttons.length > 4) {
          const pressed = left.buttons[4].pressed;
          if (pressedEdge(pressed, runtime.xPressed)) {
            const nextMicOn = !micRef.current;
            if (micRef.current) stopMicRef.current(); else void startMicRef.current();
            setMicStatus(`Microphone ${nextMicOn ? "on" : "off"}`);
            if (micStatusTimerRef.current !== null) window.clearTimeout(micStatusTimerRef.current);
            micStatusTimerRef.current = window.setTimeout(() => {
              setMicStatus(null);
              micStatusTimerRef.current = null;
            }, 3000);
          }
          runtime.xPressed = pressed;
        }
      }
      if (right && right.buttons.length > 4) {
        const pressed = right.buttons[4].pressed;
        if (pressedEdge(pressed, runtime.aPressed)) {
          runtime.scenarioMenuOpen = !runtime.scenarioMenuOpen;
          runtime.scenarioMenu.visible = runtime.scenarioMenuOpen;
          if (runtime.scenarioMenuOpen) {
            closeAllPanels();
            runtime.scenarioPage = initialVrScenarioMenuPage(
              scenarioStateRef.current.active_run,
            );
            scenarioStateRef.current.refresh();
            runtime.renderScenarioMenu();
          }
        }
        runtime.aPressed = pressed;
      }
      const rightController = runtime.controllers.find((controller) => controller.userData.handedness === "right");
      if (rightController) {
        const hoverHit = rayHitFromController(rightController);
        const reticle = rightController.userData.reticle as THREE.Mesh | undefined;
        if (reticle) {
          reticle.position.z = -(hoverHit?.distance ?? VR_RAY_MAX_DISTANCE);
          reticle.visible = true;
        }
        const hoveredNode = hoverHit ? ancestorData<VrStageNode>(hoverHit.object, "node") : undefined;
        const hoveredRoot = hoverHit ? ancestorWithData(hoverHit.object, "selectionOutline") : null;
        const hoveredTransient = hoverHit
          ? ancestorData<VrTransientEntity>(hoverHit.object, "transient")
          : undefined;
        setHoveredSelectable(
          hoveredRoot && (
            isRayGrabbableNode(hoveredNode) ||
            hoveredTransient !== undefined
          ) ? hoveredRoot : null,
        );
      } else {
        setHoveredSelectable(null);
      }
      if (runtime.avatar?.userData.motion) {
        const motion = runtime.avatar.userData.motion as AvatarMotionRuntime;
        const elapsed = Math.max(0, time - motion.startedAt);
        const walkStart = motion.turnDurationMs;
        const finalTurnStart = walkStart + motion.walkDurationMs;
        if (elapsed < walkStart && motion.turnDurationMs > 0) {
          const progress = smoothstep(Math.min(1, elapsed / motion.turnDurationMs));
          runtime.avatar.position.copy(motion.fromScene);
          runtime.avatar.quaternion.slerpQuaternions(
            motion.fromQuaternion, motion.walkQuaternion, progress,
          );
          runtime.avatar.userData.walking = false;
        } else if (elapsed < finalTurnStart && motion.walkDurationMs > 0) {
          const progress = Math.min(1, (elapsed - walkStart) / motion.walkDurationMs);
          runtime.avatar.position.lerpVectors(motion.fromScene, motion.toScene, progress);
          runtime.avatar.quaternion.copy(motion.walkQuaternion);
          runtime.avatar.userData.walking = true;
        } else if (elapsed < motion.totalDurationMs && motion.finalTurnDurationMs > 0) {
          const progress = smoothstep(Math.min(
            1, (elapsed - finalTurnStart) / motion.finalTurnDurationMs,
          ));
          runtime.avatar.position.copy(motion.toScene);
          runtime.avatar.quaternion.slerpQuaternions(
            motion.walkQuaternion, motion.finalQuaternion, progress,
          );
          runtime.avatar.userData.walking = false;
        } else {
          runtime.avatar.position.copy(motion.toScene);
          runtime.avatar.quaternion.copy(motion.finalQuaternion);
          runtime.avatar.userData.walking = false;
          delete runtime.avatar.userData.motion;
        }
      }
      if (runtime.avatar) {
        const visuals = runtime.avatar.userData.visuals as {
          body: THREE.Mesh;
          head: THREE.Mesh;
          leftArm: THREE.Mesh;
          rightArm: THREE.Mesh;
          chest: THREE.Mesh;
          halo: THREE.Mesh;
        };
        const moving = runtime.avatar.userData.walking === true;
        const offering = transientRef.current.some((entity) => entity.state === "offered");
        const seated = runtime.avatar.userData.pose === "sitting";
        const pulse = Math.sin(time * 0.0026);
        const stride = Math.sin(time * 0.009);
        visuals.body.position.y = (seated ? 0.52 : 0.72) + pulse * 0.012;
        visuals.head.position.y = (seated ? 1.05 : 1.25) + pulse * 0.018;
        visuals.leftArm.rotation.x = moving ? stride * 0.42 : pulse * 0.035;
        visuals.rightArm.rotation.x = offering ? -1.15 : moving ? -stride * 0.42 : -pulse * 0.035;
        visuals.rightArm.rotation.z = offering ? -0.38 : 0.12;
        visuals.chest.scale.setScalar(1 + Math.max(0, pulse) * 0.08);
        visuals.halo.rotation.z = time * 0.00012;

        if (runtime.activityBubble.visible) {
          const bubblePosition = runtime.avatar.getWorldPosition(new THREE.Vector3());
          bubblePosition.y += seated ? 1.48 : 1.7;
          runtime.activityBubble.position.copy(bubblePosition);
          const rigOrientation = rig.getWorldQuaternion(new THREE.Quaternion());
          const headsetPosition = liveViewerPose
            ? viewerWorldPositionFromPose(
              liveViewerPose.transform.position,
              rig.position,
              rigOrientation,
            )
            : viewerPoseCamera(renderer.xr.getCamera())
              .getWorldPosition(new THREE.Vector3());
          runtime.activityBubble.quaternion.copy(
            facingQuaternion(
              bubblePosition,
              new THREE.Vector3(headsetPosition.x, headsetPosition.y, headsetPosition.z),
            ),
          );
        }
      }
      if (currentStage) {
        const rigOrientation = rig.getWorldQuaternion(new THREE.Quaternion());
        const headsetPosition = liveViewerPose
          ? viewerWorldPositionFromPose(
            liveViewerPose.transform.position,
            rig.position,
            rigOrientation,
          )
          : viewerPoseCamera(renderer.xr.getCamera())
            .getWorldPosition(new THREE.Vector3());
        const userPosition = {
          x: headsetPosition.x / SCALE + currentStage.room.width / 2,
          y: currentStage.room.depth / 2 - headsetPosition.z / SCALE,
        };
        const avatarPosition = runtime.avatar
          ?.getWorldPosition(new THREE.Vector3());
        const agentPosition = avatarPosition ? {
          x: avatarPosition.x / SCALE + currentStage.room.width / 2,
          y: currentStage.room.depth / 2 - avatarPosition.z / SCALE,
        } : null;
        const furnitureNodes = currentStage.nodes.filter(
          (node) => node.type === "furniture",
        );
        const proximity = updateFurniturePanelProximity(
          furnitureNodes,
          userPosition,
          agentPosition,
          currentStage.world.reach_ceiling,
          {
            visible: new Set(runtime.panels.keys()),
            dismissed: runtime.dismissedPanels,
          },
        );
        runtime.dismissedPanels = new Set(proximity.dismissed);
        const desiredPanels = runtime.scenarioMenuOpen
          ? new Set<string>()
          : proximity.visible;

        [...runtime.panels.keys()].forEach((nodeId) => {
          if (!desiredPanels.has(nodeId)) closePanel(nodeId);
        });
        furnitureNodes.forEach((node) => {
          if (desiredPanels.has(node.id) && !runtime.panels.has(node.id)) {
            void openFurniturePanel(node);
          }
        });

        runtime.panels.forEach((panel, nodeId) => {
          const node = furnitureNodes.find((candidate) => candidate.id === nodeId);
          if (!node) return;
          const position = documentToScene(currentStage, node.x, node.y);
          panel.position.set(
            position.x,
            Math.min(2.25, (node.elevation + node.height / 2 + 45) * SCALE),
            position.z,
          );
          panel.rotation.y = furniturePanelRotationY(
            node.y,
            currentStage.room.depth,
          );
        });
      }
      if (currentStage) {
        const rigOrientation = rig.getWorldQuaternion(new THREE.Quaternion());
        const headset = viewerPoseCamera(renderer.xr.getCamera());
        const headsetPosition = liveViewerPose
          ? viewerWorldPositionFromPose(
            liveViewerPose.transform.position,
            rig.position,
            rigOrientation,
          )
          : headset.getWorldPosition(new THREE.Vector3());
        const forward = liveViewerPose
          ? viewerForwardFromPose(
            liveViewerPose.transform.orientation,
            rigOrientation,
          )
          : headset.getWorldDirection(new THREE.Vector3());
        const pose = {
          x: headsetPosition.x / SCALE + currentStage.room.width / 2,
          y: currentStage.room.depth / 2 - headsetPosition.z / SCALE,
          yaw: THREE.MathUtils.radToDeg(Math.atan2(forward.x, -forward.z)),
        };
        const changed = !runtime.lastPose || Math.hypot(pose.x - runtime.lastPose.x, pose.y - runtime.lastPose.y) >= 25 || Math.abs(pose.yaw - runtime.lastPose.yaw) >= 15;
        if ((changed && time - runtime.lastPoseSentAt >= 500) || time - runtime.lastPoseSentAt >= 2000) {
          runtime.poseSequence += 1;
          runtime.lastPoseSentAt = time;
          runtime.lastPose = pose;
          const value = { ...pose, head_height: Math.max(0.2, headsetPosition.y), sequence: runtime.poseSequence, updated_at: performance.now() };
          setUserPose(value);
          wsService.sendMessage({ type: "vr-user-pose", pose: value });
        }
      }
      renderer.render(scene, camera);
    };
    const panelRefreshInterval = window.setInterval(() => {
      [...runtime.panels.keys()].forEach((nodeId) => {
        const panel = runtime.panels.get(nodeId);
        const provider = panel?.userData.provider as string | undefined;
        if (panel && provider && INTERACTIVE_FURNITURE_PROVIDERS.has(provider)) {
          void refreshPanel(nodeId);
        }
      });
    }, FURNITURE_PANEL_REFRESH_MS);
    renderer.setAnimationLoop(renderFrame);
    void renderer.xr.setSession(xrSession).then(() => renderer.xr.setFoveation(0.4));

    return () => {
      window.clearInterval(panelRefreshInterval);
      renderer.setAnimationLoop(null);
      controllers.forEach((controller) => controller.removeEventListener("select", handleSelect as never));
      controllers.forEach((controller) => controller.removeEventListener("connected", handleConnected as never));
      controllers.forEach((controller) => controller.removeEventListener("disconnected", handleDisconnected as never));
      controllers.forEach((controller) => controller.removeEventListener("squeezestart", handleSqueezeStart as never));
      controllers.forEach((controller) => controller.removeEventListener("squeezeend", handleSqueezeEnd as never));
      disposeObject(scene);
      renderer.dispose();
      if (renderer.domElement.parentElement === host) host.removeChild(renderer.domElement);
      runtimeRef.current = null;
    };
  }, [xrSession, selectionHighlightEnabled]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime || !stage) return;
    let disposed = false;
    const { furniture, scene } = runtime;
    runtime.hoveredSelectable = null;
    while (furniture.children.length) {
      const child = furniture.children[furniture.children.length - 1];
      furniture.remove(child);
      disposeObject(child);
    }
    const detachedProps: THREE.Object3D[] = [];
    runtime.scene.traverse((object) => {
      const node = object.userData.node as VrStageNode | undefined;
      const parentNode = object.parent?.userData.node as VrStageNode | undefined;
      if (node?.type === "prop" && object.parent !== furniture && parentNode?.type !== "prop") detachedProps.push(object);
    });
    detachedProps.forEach((object) => {
      object.parent?.remove(object);
      disposeObject(object);
    });
    if (runtime.avatar) {
      [...runtime.avatar.children].forEach((child) => {
        const node = child.userData.node as VrStageNode | undefined;
        if (node?.type !== "prop") return;
        runtime.avatar?.remove(child);
        disposeObject(child);
      });
    }
    const textureLoader = new THREE.TextureLoader();
    const warnUnavailable = (key: string, message: string) => {
      if (runtime.textureWarnings.has(key)) return;
      runtime.textureWarnings.add(key);
      setVrWarning(message);
      if (warningTimerRef.current !== null) window.clearTimeout(warningTimerRef.current);
      warningTimerRef.current = window.setTimeout(() => {
        setVrWarning(null);
        warningTimerRef.current = null;
      }, 3000);
    };
    runtime.collisionNodes = stage.nodes.filter((node) => node.type !== "prop" || !propStates[node.id]);
    stage.nodes.filter((node) => node.type === "furniture" || node.type === "prop").forEach((node) => {
      const root = new THREE.Group();
      const position = documentToScene(stage, node.x, node.y);
      root.position.set(position.x, furnitureCenterSceneY(node.elevation), position.z);
      root.rotation.y = THREE.MathUtils.degToRad(node.yaw || 0);
      root.userData.node = node;

      const solid = new THREE.MeshStandardMaterial({
        color: node.color, roughness: 0.64, metalness: 0.035,
      });
      const materials: THREE.Material[] = Array.from({ length: 6 }, () => solid.clone());
      solid.dispose();
      const fallback = new THREE.Mesh(
        new THREE.BoxGeometry(node.width * SCALE, node.height * SCALE, node.depth * SCALE),
        materials,
      );
      fallback.castShadow = true;
      fallback.receiveShadow = true;
      fallback.userData.node = node;
      root.add(fallback);

      const asset = node.type === "furniture" ? furnitureModelAsset(node.id) : undefined;
      const decalRoot = new THREE.Group();
      decalRoot.userData.node = node;
      root.add(decalRoot);

      const trimModelDecals = () => {
        [...decalRoot.children].forEach((child) => {
          if (asset?.keepFrontTexture && child.userData.decalFace === "-z") return;
          decalRoot.remove(child);
          disposeObject(child);
        });
      };

      if (node.texture?.src) {
        textureLoader.load(
          node.texture.src,
          (texture) => {
            if (disposed || !root.parent) {
              texture.dispose();
              return;
            }
            texture.colorSpace = THREE.SRGBColorSpace;
            texture.magFilter = THREE.LinearFilter;
            texture.minFilter = THREE.LinearMipmapLinearFilter;
            texture.anisotropy = Math.min(8, runtime.renderer.capabilities.getMaxAnisotropy());
            const image = texture.image as { width?: number; height?: number };
            const layouts = furnitureDecalLayouts(
              node,
              { width: image.width || 1, height: image.height || 1 },
            );
            const visibleLayouts = root.userData.modelLoaded
              ? layouts.filter((layout) => asset?.keepFrontTexture && layout.face === "-z")
              : layouts;
            visibleLayouts.forEach((layout) => {
              const decalTexture = texture.clone();
              decalTexture.needsUpdate = true;
              const decal = new THREE.Mesh(
                new THREE.PlaneGeometry(layout.width * SCALE, layout.height * SCALE),
                new THREE.MeshStandardMaterial({
                  map: decalTexture,
                  transparent: true,
                  roughness: 0.55,
                  side: THREE.DoubleSide,
                  depthWrite: false,
                }),
              );
              decal.position.set(layout.x * SCALE, 0, layout.z * SCALE);
              decal.rotation.y = layout.rotationY;
              decal.renderOrder = 1;
              decal.userData.node = node;
              decal.userData.decalFace = layout.face;
              decalRoot.add(decal);
            });
            texture.dispose();
          },
          undefined,
          () => {
            warnUnavailable(
              `texture:${node.id}`,
              `Texture unavailable: ${node.label || node.id}`,
            );
          },
        );
      }
      const edge = new THREE.LineSegments(
        new THREE.EdgesGeometry(fallback.geometry, 24),
        new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.1 }),
      );
      edge.raycast = () => undefined;
      root.add(edge);
      root.userData.selectionOutline = edge;
      root.traverse((child) => { child.userData.node = node; });
      const propState = node.type === "prop" ? propStates[node.id] : undefined;
      if (propState && runtime.avatar) {
        root.position.set(0.42, propState === "offered" ? 0.92 : 0.74, 0.28);
        root.rotation.y = 0;
        root.userData.offeredProp = propState === "offered";
        runtime.avatar.add(root);
      } else {
        furniture.add(root);
      }

      if (asset) {
        void loadFurnitureModel(asset).then((model) => {
          if (disposed || !root.parent) {
            disposeObject(model);
            return;
          }
          model.rotation.y = asset.rotationY || 0;
          const visual = new THREE.Group();
          visual.add(model);
          const fitted = fitFurnitureModel(visual, {
            width: node.width * SCALE,
            height: node.height * SCALE,
            depth: node.depth * SCALE,
          });
          if (!fitted) {
            disposeObject(visual);
            warnUnavailable(
              `model:${node.id}`,
              `Model unavailable: ${node.label || node.id}`,
            );
            return;
          }
          visual.userData.node = node;
          visual.traverse((child) => { child.userData.node = node; });
          root.userData.modelLoaded = true;
          trimModelDecals();
          root.remove(fallback);
          disposeObject(fallback);
          root.add(visual);
        }).catch(() => {
          if (disposed || !root.parent) return;
          warnUnavailable(
            `model:${node.id}`,
            `Model unavailable: ${node.label || node.id}`,
          );
        });
      }
    });

    const roomWidth = stage.room.width * SCALE;
    const roomDepth = stage.room.depth * SCALE;
    const roomHeight = stage.room.height * SCALE;
    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(roomWidth, roomDepth),
      new THREE.MeshStandardMaterial({
        color: stage.room.floor_color, roughness: 0.92, metalness: 0, side: THREE.DoubleSide,
      }),
    );
    floor.rotation.x = -Math.PI / 2;
    floor.receiveShadow = true;
    furniture.add(floor);
    const grid = new THREE.GridHelper(roomWidth, 24, 0xffffff, 0xffffff);
    grid.raycast = () => undefined;
    grid.scale.z = roomDepth / roomWidth;
    grid.position.y = 0.004;
    const gridMaterials = Array.isArray(grid.material) ? grid.material : [grid.material];
    gridMaterials.forEach((material) => {
      material.transparent = true;
      material.opacity = 0.055;
      material.depthWrite = false;
    });
    furniture.add(grid);

    const wallMaterial = new THREE.MeshStandardMaterial({
      color: stage.room.wall_color, roughness: 0.88, metalness: 0, side: THREE.DoubleSide,
    });
    const back = new THREE.Mesh(new THREE.PlaneGeometry(roomWidth, roomHeight), wallMaterial);
    back.position.set(0, roomHeight / 2, roomDepth / 2);
    back.rotation.y = Math.PI;
    back.receiveShadow = true;
    furniture.add(back);
    const front = back.clone(); front.position.z = -roomDepth / 2; front.rotation.y = 0; furniture.add(front);
    const sideGeometry = new THREE.PlaneGeometry(roomDepth, roomHeight);
    const leftWall = new THREE.Mesh(sideGeometry, wallMaterial.clone());
    leftWall.position.set(-roomWidth / 2, roomHeight / 2, 0); leftWall.rotation.y = Math.PI / 2; furniture.add(leftWall);
    const rightWall = leftWall.clone(); rightWall.position.x = roomWidth / 2; rightWall.rotation.y = -Math.PI / 2; furniture.add(rightWall);
    const trimMaterial = new THREE.MeshStandardMaterial({ color: 0xf5ead9, roughness: 0.7 });
    const horizontalTrim = new THREE.BoxGeometry(roomWidth, 0.07, 0.06);
    const verticalTrim = new THREE.BoxGeometry(0.06, 0.07, roomDepth);
    [
      new THREE.Mesh(horizontalTrim, trimMaterial),
      new THREE.Mesh(horizontalTrim, trimMaterial.clone()),
    ].forEach((trim, index) => {
      trim.position.set(0, 0.035, index === 0 ? -roomDepth / 2 + 0.03 : roomDepth / 2 - 0.03);
      trim.castShadow = true;
      furniture.add(trim);
    });
    [
      new THREE.Mesh(verticalTrim, trimMaterial.clone()),
      new THREE.Mesh(verticalTrim, trimMaterial.clone()),
    ].forEach((trim, index) => {
      trim.position.set(index === 0 ? -roomWidth / 2 + 0.03 : roomWidth / 2 - 0.03, 0.035, 0);
      trim.castShadow = true;
      furniture.add(trim);
    });

    const avatarNode = stage.nodes.find((node) => node.id === "avatar");
    if (avatarNode) {
      if (!runtime.avatar) {
        runtime.avatar = createSpatialAvatar(avatarNode);
        runtime.avatar.userData.positionInitialized = false;
        scene.add(runtime.avatar);
      }
      const target = documentToScene(stage, avatarNode.x, avatarNode.y);
      if (runtime.avatar.userData.positionInitialized !== true) {
        runtime.avatar.position.copy(target);
        runtime.avatar.userData.positionInitialized = true;
      }
      else if (
        motionPlan &&
        runtime.avatar.userData.motionSequence !== motionPlan.sequence &&
        Math.abs(motionPlan.to.x - avatarNode.x) < 0.01 &&
        Math.abs(motionPlan.to.y - avatarNode.y) < 0.01
      ) {
        const fromScene = runtime.avatar.position.clone();
        const walkQuaternion = facingQuaternion(fromScene, target);
        const finalFaceScene = motionPlan.finalFacePoint
          ? documentToScene(stage, motionPlan.finalFacePoint.x, motionPlan.finalFacePoint.y)
          : null;
        runtime.avatar.userData.motionSequence = motionPlan.sequence;
        runtime.avatar.userData.motion = {
          ...motionPlan,
          startedAt: performance.now(),
          fromScene,
          toScene: target,
          fromQuaternion: runtime.avatar.quaternion.clone(),
          walkQuaternion,
          finalQuaternion: finalFaceScene
            ? facingQuaternion(target, finalFaceScene)
            : walkQuaternion.clone(),
        } satisfies AvatarMotionRuntime;
      } else if (!runtime.avatar.userData.motion && !runtime.avatar.position.equals(target)) {
        runtime.avatar.position.copy(target);
      }
      runtime.avatar.userData.pose = avatarNode.pose;
    }
    if (!runtime.rig.userData.spawned) {
      const spawnFloor = avatarNode
        ? selectViewerSpawn(
          avatarNode,
          stage.nodes.filter((node) => node.type === "furniture"),
          stage.room,
        )
        : { x: stage.room.width / 2, y: stage.room.depth / 2 };
      const spawn = documentToScene(stage, spawnFloor.x, spawnFloor.y);
      runtime.rig.position.set(spawn.x, 0, spawn.z);
      if (avatarNode) {
        const companion = documentToScene(stage, avatarNode.x, avatarNode.y);
        runtime.rig.rotation.y = Math.atan2(-(companion.x - spawn.x), -(companion.z - spawn.z));
      }
      runtime.rig.userData.spawned = true;
    }
    return () => { disposed = true; };
  }, [stage, motionPlan, propStates]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    const oldTransientObjects: THREE.Object3D[] = [];
    runtime.scene.traverse((object) => { if (object.userData.transient) oldTransientObjects.push(object); });
    oldTransientObjects.forEach((child) => {
      child.parent?.remove(child);
      disposeObject(child);
    });
    transients.forEach((entity) => {
      const bodyPage = entity.kind === "email_card" && entity.page && entity.body
        ? entity.body.slice((entity.page - 1) * 800, entity.page * 800)
        : "";
      const lines = entity.kind === "email_card"
        ? (bodyPage
          ? [entity.subject || entity.title, ...(bodyPage.match(/.{1,72}/g)?.slice(0, 8) || []), `Page ${entity.page}/${Math.ceil((entity.body || "").length / 800)} · Trigger to return`]
          : [entity.subject || entity.title, entity.sender || "", entity.summary || "", "Trigger to inspect"])
        : [entity.title, ...entity.blocks];
      const mesh = new THREE.Mesh(
        new THREE.PlaneGeometry(entity.kind === "email_card" ? 0.64 : 1.2, entity.kind === "email_card" ? 0.42 : 0.62),
        new THREE.MeshBasicMaterial({ map: canvasTexture(lines, 768, entity.kind === "email_card" ? 504 : 360), transparent: true, side: THREE.DoubleSide }),
      );
      mesh.userData.transient = entity;
      const edge = new THREE.LineSegments(
        new THREE.EdgesGeometry(mesh.geometry),
        new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.1 }),
      );
      edge.raycast = () => undefined;
      mesh.add(edge);
      mesh.userData.selectionOutline = edge;
      const closeX = entity.kind === "email_card" ? 0.25 : 0.52;
      const closeY = entity.kind === "email_card" ? 0.14 : 0.23;
      const closeButton = new THREE.Mesh(
        new THREE.PlaneGeometry(0.12, 0.12),
        new THREE.MeshBasicMaterial({
          map: canvasTexture(["×"], 128, 128), transparent: true, depthTest: false,
        }),
      );
      closeButton.position.set(closeX, closeY, 0.012);
      closeButton.renderOrder = 3;
      closeButton.userData.transientClose = entity.id;
      mesh.add(closeButton);
      const closeHitTarget = new THREE.Mesh(
        new THREE.PlaneGeometry(0.24, 0.22),
        new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false }),
      );
      closeHitTarget.position.set(closeX, closeY, 0.018);
      closeHitTarget.userData.transientClose = entity.id;
      mesh.add(closeHitTarget);
      if ((entity.state === "held_by_agent" || entity.state === "offered") && runtime.avatar) {
        mesh.position.set(0.42, entity.state === "offered" ? 0.92 : 0.74, 0.28);
        mesh.rotation.y = Math.PI;
        runtime.avatar.add(mesh);
      } else {
        const point = documentToScene(stage!, entity.x, entity.y);
        mesh.position.set(point.x, entity.elevation * SCALE, point.z);
        mesh.rotation.y = THREE.MathUtils.degToRad(entity.yaw);
        runtime.transientRoot.add(mesh);
      }
    });
  }, [transients, stage]);

  useEffect(() => {
    const timers = transients.filter((entity) => entity.state === "offered").map((entity) => window.setTimeout(() => {
      const runtime = runtimeRef.current;
      const current = transientRef.current.find((item) => item.id === entity.id);
      const currentStage = stageRef.current;
      const avatar = currentStage?.nodes.find((node) => node.id === "avatar");
      if (!runtime || !current || current.state !== "offered" || !avatar) return;
      updateTransient({ ...current, state: "world_locked", x: avatar.x, y: avatar.y, elevation: 105 });
      runtime.eventSequence += 1;
      wsService.sendMessage({ type: "vr-physical-events", sequence: runtime.eventSequence, events: [{ kind: "offer_ignored", entity_id: current.id }] });
    }, 30_000));
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [transients, updateTransient]);

  useEffect(() => {
    const timers = Object.entries(propStates).filter(([, state]) => state === "offered").map(([entityId]) => window.setTimeout(() => {
      const runtime = runtimeRef.current;
      if (!runtime || propStates[entityId] !== "offered") return;
      updatePropState(entityId, "held_by_agent");
      runtime.eventSequence += 1;
      wsService.sendMessage({ type: "vr-physical-events", sequence: runtime.eventSequence, events: [{ kind: "offer_ignored", entity_id: entityId }] });
    }, 30_000));
    return () => timers.forEach((timer) => window.clearTimeout(timer));
  }, [propStates, updatePropState]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    const activityText = currentActivityLabel(activity, {
      thinking: t("agentActivity.thinking"),
      planning: t("agentActivity.planningNext"),
      unknownTool: t("agentActivity.unknownTask"),
    });
    runtime.activityBubble.visible = Boolean(activityText);
    if (!activityText) return;
    const next = activityBubbleTexture(activityText);
    (runtime.activityBubble.material as THREE.MeshBasicMaterial).map = next;
    (runtime.activityBubble.material as THREE.MeshBasicMaterial).needsUpdate = true;
    runtime.activityBubbleTexture.dispose();
    runtime.activityBubbleTexture = next;
  }, [activity, t, xrSession]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    const spokenCaption = audioPlaying && showSubtitle ? subtitleText.trim() : "";
    const shouldShow = Boolean(micStatus || spokenCaption);
    runtime.hud.visible = shouldShow;
    if (!shouldShow) return;
    const status = micStatus ? "Microphone" : "Speaking";
    const caption = micStatus || spokenCaption;
    const next = captionTexture(caption, status);
    (runtime.hud.material as THREE.MeshBasicMaterial).map = next;
    (runtime.hud.material as THREE.MeshBasicMaterial).needsUpdate = true;
    runtime.hudTexture.dispose();
    runtime.hudTexture = next;
  }, [subtitleText, showSubtitle, audioPlaying, micStatus, xrSession]);

  useEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return;
    const warning = audioWarning
      || vrWarning
      || (wsState !== "OPEN" ? "Reconnecting to your companion" : "");
    runtime.systemHud.visible = Boolean(warning);
    if (!warning) return;
    const status = audioWarning ? "Audio" : vrWarning ? "VR" : wsState.toLowerCase();
    const next = captionTexture(warning, status);
    (runtime.systemHud.material as THREE.MeshBasicMaterial).map = next;
    (runtime.systemHud.material as THREE.MeshBasicMaterial).needsUpdate = true;
    runtime.systemHudTexture.dispose();
    runtime.systemHudTexture = next;
  }, [audioWarning, vrWarning, wsState, xrSession]);

  return (
    <>
      <div ref={hostRef} style={{ position: "fixed", inset: 0, background: "#0c1120" }} />
      <Button
        position="fixed"
        top="max(18px, env(safe-area-inset-top))"
        right="max(18px, env(safe-area-inset-right))"
        zIndex="10"
        height="38px"
        px="4"
        gap="2"
        color="#e2e8f0"
        bg="rgba(8, 12, 24, 0.72)"
        border="1px solid rgba(255,255,255,0.16)"
        borderRadius="full"
        backdropFilter="blur(20px)"
        _hover={{ bg: "rgba(15, 23, 42, 0.9)" }}
        onClick={() => void leaveVr()}
        aria-label="Exit immersive VR"
      >
        <FiX />
        Exit VR
      </Button>
    </>
  );
}
