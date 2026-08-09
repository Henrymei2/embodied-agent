import { useEffect, useRef, useState } from "react";
import { Button } from "@chakra-ui/react";
import { FiX } from "react-icons/fi";
import * as THREE from "three";
import {
  useVrStage, VrAvatarMotionPlan, VrStageNode, VrTransientEntity,
} from "@/context/vr-stage-context";
import { useRendererMode } from "@/context/renderer-mode-context";
import { useVAD } from "@/context/vad-context";
import { useSubtitle } from "@/context/subtitle-context";
import { useAgentActivity } from "@/context/agent-activity-context";
import { useWebSocket } from "@/context/websocket-context";
import { wsService } from "@/services/websocket-service";
import { audioManager } from "@/utils/audio-manager";
import {
  fitFurnitureModel,
  furnitureModelAsset,
  loadFurnitureModel,
} from "@/utils/vr-furniture-models";
import {
  floorToScene,
  furnitureCenterSceneY,
  furnitureDecalLayouts,
  headRelativeDelta,
  horizontalBillboardTarget,
  inputSourcesByHand,
  isViewerPositionClear,
  pressedEdge,
  preferPhysicalVrRayHits,
  isRayGrabbableNode,
  nextEmailPage,
  questLocomotionStick,
  rescaleThumbstick,
  selectVrRayHit,
  selectionProxyDimensions,
  selectViewerSpawn,
  snapTurnStep,
  transientTriggerBehavior,
  clampCaptionText,
  viewerForwardFromPose,
  viewerPoseCamera,
  VR_RAY_MAX_DISTANCE,
  loadVrSelectionHighlight,
} from "@/utils/vr-helpers";

const SCALE = 0.01;
const MOVE_SPEED = 1.5;
const DEAD_ZONE = 0.2;
const SNAP_THRESHOLD = 0.7;
const SNAP_REARM = 0.3;
const SNAP_RADIANS = THREE.MathUtils.degToRad(30);
const PANEL_FACING_DAMPING = 8;

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
  panel: THREE.Mesh | null;
  raycaster: THREE.Raycaster;
  controllers: THREE.XRTargetRaySpace[];
  hud: THREE.Mesh;
  hudTexture: THREE.Texture;
  turnArmed: boolean;
  xPressed: boolean;
  lastFrame: number;
  collisionNodes: VrStageNode[];
  panelGeneration: number;
  textureWarnings: Set<string>;
  grabbed: { object: THREE.Object3D; entityId: string; kind: "prop" | "transient"; wasOffered: boolean } | null;
  poseSequence: number;
  eventSequence: number;
  lastPoseSentAt: number;
  lastPose: { x: number; y: number; yaw: number } | null;
  hoveredSelectable: THREE.Object3D | null;
}

interface PanelAction {
  collection: "plans" | "skills";
  id: string;
  decision: "approved" | "declined";
}

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

function canvasTexture(lines: string[], width = 768, height = 256) {
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
  lines.slice(0, 5).forEach((line, index) => {
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

function summaryLines(value: unknown): string[] {
  if (!value || typeof value !== "object") return [];
  const entries = Object.entries(value as Record<string, unknown>);
  return entries.slice(0, 4).map(([key, item]) => {
    if (typeof item === "string" || typeof item === "number" || typeof item === "boolean") return `${key}: ${String(item)}`;
    if (Array.isArray(item)) return `${key}: ${item.length} item(s)`;
    return `${key}: available`;
  });
}

export default function VrStage() {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const runtimeRef = useRef<VrRuntime | null>(null);
  const { stage, motionPlan, transients, propStates, setUserPose, updateTransient, removeTransient, updatePropState } = useVrStage();
  const transientRef = useRef(transients);
  const stageRef = useRef(stage);
  const { xrSession, leaveVr } = useRendererMode();
  const { micOn, startMic, stopMic } = useVAD();
  const micRef = useRef(micOn);
  const { subtitleText } = useSubtitle();
  const { activity } = useAgentActivity();
  const { baseUrl, wsState } = useWebSocket();
  const baseUrlRef = useRef(baseUrl);
  const startMicRef = useRef(startMic);
  const stopMicRef = useRef(stopMic);
  const micStatusTimerRef = useRef<number | null>(null);
  const warningTimerRef = useRef<number | null>(null);
  const [micStatus, setMicStatus] = useState<string | null>(null);
  const [vrWarning, setVrWarning] = useState<string | null>(null);
  const [audioWarning, setAudioWarning] = useState<string | null>(null);
  const initialMicOnRef = useRef(micOn);
  const selectionHighlightEnabled = loadVrSelectionHighlight();

  useEffect(() => { stageRef.current = stage; }, [stage]);
  useEffect(() => { transientRef.current = transients; }, [transients]);
  useEffect(() => { micRef.current = micOn; }, [micOn]);
  useEffect(() => { baseUrlRef.current = baseUrl; }, [baseUrl]);
  useEffect(() => { startMicRef.current = startMic; }, [startMic]);
  useEffect(() => { stopMicRef.current = stopMic; }, [stopMic]);

  useEffect(() => {
    if (xrSession && initialMicOnRef.current) void startMicRef.current();
  }, [xrSession]);

  useEffect(() => () => {
    if (micStatusTimerRef.current !== null) window.clearTimeout(micStatusTimerRef.current);
    if (warningTimerRef.current !== null) window.clearTimeout(warningTimerRef.current);
  }, []);

  useEffect(() => audioManager.subscribe((state) => {
    setAudioWarning(state.state === "error" ? state.message : null);
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
      new THREE.MeshBasicMaterial({ map: hudTexture, transparent: true, depthTest: false }),
    );
    hud.position.set(0, -0.48, -1.62);
    hud.renderOrder = 100;
    camera.add(hud);

    const runtime: VrRuntime = {
      scene, renderer, camera, rig, furniture, transientRoot, avatar: null, panel: null,
      raycaster: new THREE.Raycaster(undefined, undefined, 0, VR_RAY_MAX_DISTANCE), controllers, hud, hudTexture,
      turnArmed: true, xPressed: false, lastFrame: performance.now(),
      collisionNodes: [],
      panelGeneration: 0,
      textureWarnings: new Set<string>(),
      grabbed: null, poseSequence: 0, eventSequence: 0, lastPoseSentAt: 0, lastPose: null,
      hoveredSelectable: null,
    };
    runtimeRef.current = runtime;

    const setPanelLines = (panel: THREE.Mesh, lines: string[]) => {
      const material = panel.material as THREE.MeshBasicMaterial;
      const previous = material.map;
      material.map = canvasTexture(lines, 768, 300);
      material.needsUpdate = true;
      previous?.dispose();
    };

    const closePanel = () => {
      runtime.panelGeneration += 1;
      if (!runtime.panel) return;
      scene.remove(runtime.panel);
      disposeObject(runtime.panel);
      runtime.panel = null;
    };

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
      ...(runtime.panel ? [runtime.panel] : []),
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
        preferPhysicalVrRayHits(hits),
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

    const selectFurniture = async (node: VrStageNode) => {
      const closingCurrent = runtime.panel?.userData.nodeId === node.id;
      closePanel();
      if (closingCurrent) return;
      const windowConfig: NonNullable<VrStageNode["uiWindow"]> = node.uiWindow || {
        title: node.label || node.id,
        body: "No additional information is available for this furniture.",
      };
      const generation = runtime.panelGeneration;
      const lines = [windowConfig.title, windowConfig.body || "", "Loading…"];
      const panelTexture = canvasTexture(lines, 768, 300);
      const panel = new THREE.Mesh(
        new THREE.PlaneGeometry(1.5, 0.59),
        new THREE.MeshBasicMaterial({ map: panelTexture, transparent: true, side: THREE.DoubleSide }),
      );
      const position = documentToScene(stageRef.current!, node.x, node.y);
      panel.position.set(position.x, Math.min(2.25, (node.elevation + node.height / 2 + 45) * SCALE), position.z);
      const headset = viewerPoseCamera(renderer.xr.getCamera());
      const headsetPosition = new THREE.Vector3();
      headset.getWorldPosition(headsetPosition);
      panel.lookAt(headsetPosition.x, panel.position.y, headsetPosition.z);
      panel.userData.panel = true;
      panel.userData.node = node;
      panel.userData.nodeId = node.id;
      addCloseControl(panel, new THREE.Vector3(0.63, 0.22, 0.012), { panelClose: true });
      scene.add(panel);
      runtime.panel = panel;

      const route = windowConfig.provider ? providerRoutes[windowConfig.provider] : undefined;
      let liveData: Record<string, unknown> | null = null;
      if (route) {
        try {
          const response = await fetch(`${baseUrlRef.current}${route}`, { headers: { Accept: "application/json" } });
          if (response.ok) {
            liveData = await response.json() as Record<string, unknown>;
            lines.splice(2, 0, ...summaryLines(liveData));
            if (windowConfig.provider === "google_calendar_status" && liveData.connected !== true) {
              lines.splice(2, 0, "Exit VR to connect Google");
            }
          }
        } catch (_error) {
          lines.splice(2, 0, "Live data unavailable");
        }
      }
      if (runtime.panel !== panel || runtime.panelGeneration !== generation) return;
      lines.splice(lines.indexOf("Loading…"), 1, "Select the furniture again to close");
      setPanelLines(panel, lines);
      const pending = Array.isArray(liveData?.pending) ? liveData.pending[0] as Record<string, unknown> | undefined : undefined;
      const collection = windowConfig.provider === "plan_status" ? "plans" : windowConfig.provider === "skill_status" ? "skills" : null;
      const decisionId = collection === "plans" ? pending?.plan_id : pending?.slug;
      if (collection && typeof decisionId === "string") {
        (["approved", "declined"] as const).forEach((decision, index) => {
          const button = new THREE.Mesh(
            new THREE.PlaneGeometry(0.55, 0.15),
            new THREE.MeshBasicMaterial({
              map: canvasTexture([decision === "approved" ? "Approve" : "Decline"], 320, 96),
              transparent: true,
            }),
          );
          button.position.set(index === 0 ? -0.32 : 0.32, -0.39, 0.01);
          button.userData.panelAction = { collection, id: decisionId, decision } satisfies PanelAction;
          panel.add(button);
        });
      }
    };

    const activatePanelAction = async (action: PanelAction) => {
      try {
        const response = await fetch(`${baseUrlRef.current}/api/furniture/${action.collection}/${encodeURIComponent(action.id)}/decision`, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ decision: action.decision }),
        });
        if (!response.ok) throw new Error("Decision was rejected");
      } catch (_error) {
        // The furniture panel remains open so the user can retry.
      }
    };

    const handleSelect = (event: THREE.Event & { target: THREE.Object3D }) => {
      const controller = event.target;
      if (controller.userData.handedness !== "right") return;
      const hit = rayHitFromController(controller);
      if (!hit) return;
      const panelAction = hit.object.userData.panelAction as PanelAction | undefined;
      if (panelAction) {
        void activatePanelAction(panelAction);
        return;
      }
      if (hit.object.userData.panelClose === true) {
        closePanel();
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
      const node = ancestorData<VrStageNode>(hit.object, "node");
      if (node) void selectFurniture(node);
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
      if (left && left.axes.length >= 4 && currentStage) {
        const rawAxes = questLocomotionStick(left.axes[2] || 0, left.axes[3] || 0);
        const axes = rescaleThumbstick(rawAxes.x, rawAxes.y, DEAD_ZONE);
        const referenceSpace = renderer.xr.getReferenceSpace();
        const viewerPose = frame && referenceSpace
          ? frame.getViewerPose(referenceSpace)
          : null;
        const forward = viewerPose
          ? viewerForwardFromPose(
            viewerPose.transform.orientation,
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
      if (right && right.axes.length >= 3) {
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
            }, 1500);
          }
          runtime.xPressed = pressed;
        }
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
            hoveredNode?.type === "furniture" ||
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
      }
      if (runtime.panel) {
        const headsetPosition = viewerPoseCamera(renderer.xr.getCamera())
          .getWorldPosition(new THREE.Vector3());
        const facingTarget = horizontalBillboardTarget(runtime.panel.position, headsetPosition);
        const target = facingQuaternion(runtime.panel.position, new THREE.Vector3(
          facingTarget.x, facingTarget.y, facingTarget.z,
        ));
        runtime.panel.quaternion.slerp(target, 1 - Math.exp(-PANEL_FACING_DAMPING * dt));
      }
      if (currentStage) {
        const headset = viewerPoseCamera(renderer.xr.getCamera());
        const headsetPosition = new THREE.Vector3();
        const forward = headset.getWorldDirection(new THREE.Vector3());
        headset.getWorldPosition(headsetPosition);
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
    renderer.setAnimationLoop(renderFrame);
    void renderer.xr.setSession(xrSession).then(() => renderer.xr.setFoveation(0.4));

    return () => {
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
      if (node.type === "furniture") {
        const proxy = selectionProxyDimensions(node);
        const selectionProxy = new THREE.Mesh(
          new THREE.BoxGeometry(
            proxy.width,
            proxy.height,
            proxy.depth,
          ),
          new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false }),
        );
        selectionProxy.userData.node = node;
        selectionProxy.userData.selectionProxy = true;
        root.add(selectionProxy);
      }
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
    const activityText = activity.phase === "idle" ? "" : activity.phase.replace(/-/g, " ");
    const shouldShow = Boolean(subtitleText.trim() || activityText || micStatus || vrWarning || audioWarning || wsState !== "OPEN");
    runtime.hud.visible = shouldShow;
    if (!shouldShow) return;
    const status = [
      wsState === "OPEN" ? "Connected" : wsState.toLowerCase(),
      activityText,
      micStatus,
      vrWarning,
      audioWarning,
    ].filter(Boolean).join(" · ");
    const caption = audioWarning || subtitleText.trim() || vrWarning || micStatus || activityText || "Reconnecting to your companion";
    const next = captionTexture(caption, status);
    (runtime.hud.material as THREE.MeshBasicMaterial).map = next;
    runtime.hudTexture.dispose();
    runtime.hudTexture = next;
  }, [subtitleText, activity, micStatus, vrWarning, audioWarning, wsState]);

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
