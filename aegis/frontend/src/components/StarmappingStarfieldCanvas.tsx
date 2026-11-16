import { useEffect, useRef } from 'react';

import starfieldBackground from '../assets/starmapping/starmapping-starfield-background.webp';

interface StarfieldPoint {
  x: number;
  y: number;
}

export interface StarfieldExclusion extends StarfieldPoint {
  radius: number;
}

export interface BackgroundParticle extends StarfieldPoint {
  id: number;
  alpha: number;
  color: string;
  radius: number;
}

interface ActivePulse {
  delay: number;
  duration: number;
  startedAt: number;
}

export interface StarfieldParticleProfile {
  baseRadiusMultiplier: number;
  glowRadiusMultiplier: number;
  maxActiveParticles: number;
}

const TOPOLOGY_VIEWBOX = { width: 1000, height: 700, centerX: 500, centerY: 365 };
const OUTER_STARFIELD_RADIUS = 312;
const MAX_ACTIVE_PARTICLES = 100;
const PULSE_INTERVAL_MS = 2_000;
const PULSE_WAVE_STAGGER_MS = 800;
const PULSE_MAX_LIFETIME_MS = 1_800;

const THEME_PARTICLE_TOKENS = [
  '--aegis-starmap-accent',
  '--aegis-starmap-focus',
  '--aegis-starmap-actor',
  '--aegis-starmap-success',
];

interface StarfieldThemePalette {
  background: string;
  particles: string[];
}

const FULLSCREEN_PARTICLE_PROFILE: StarfieldParticleProfile = {
  baseRadiusMultiplier: 1,
  glowRadiusMultiplier: 7,
  maxActiveParticles: MAX_ACTIVE_PARTICLES,
};

const INLINE_PARTICLE_PROFILE: StarfieldParticleProfile = {
  baseRadiusMultiplier: 0.5,
  glowRadiusMultiplier: 3,
  maxActiveParticles: 50,
};

export function getStarfieldParticleProfile(isFullscreen: boolean): StarfieldParticleProfile {
  return isFullscreen ? FULLSCREEN_PARTICLE_PROFILE : INLINE_PARTICLE_PROFILE;
}

export function calculateStarfieldExclusion(width: number, height: number): StarfieldExclusion {
  const scale = Math.min(width / TOPOLOGY_VIEWBOX.width, height / TOPOLOGY_VIEWBOX.height);
  return {
    x: (width - (TOPOLOGY_VIEWBOX.width * scale)) / 2 + (TOPOLOGY_VIEWBOX.centerX * scale),
    y: (height - (TOPOLOGY_VIEWBOX.height * scale)) / 2 + (TOPOLOGY_VIEWBOX.centerY * scale),
    radius: OUTER_STARFIELD_RADIUS * scale,
  };
}

function resolveStarfieldThemePalette(): StarfieldThemePalette {
  const style = window.getComputedStyle(document.documentElement);
  const particles = THEME_PARTICLE_TOKENS
    .map((token) => style.getPropertyValue(token).trim())
    .filter(Boolean);
  return {
    background: style.getPropertyValue('--aegis-starmap-bg').trim() || 'transparent',
    particles: particles.length ? particles : ['transparent'],
  };
}

export function isOutsideStarfieldExclusion(point: StarfieldPoint, exclusion: StarfieldExclusion): boolean {
  return Math.hypot(point.x - exclusion.x, point.y - exclusion.y) > exclusion.radius;
}

export function splitParticlePulseWaves(ids: number[]): [number[], number[]] {
  const firstWaveSize = Math.ceil(ids.length / 2);
  return [ids.slice(0, firstWaveSize), ids.slice(firstWaveSize)];
}

export function selectParticlePulseBatch(
  particles: BackgroundParticle[],
  activeIds: ReadonlySet<number>,
  random: () => number = Math.random,
  maximumActiveParticles = MAX_ACTIVE_PARTICLES,
): number[] {
  const capacity = Math.max(0, maximumActiveParticles - activeIds.size);
  const candidates = particles.filter((particle) => !activeIds.has(particle.id));
  const shuffled = [...candidates];

  for (let index = shuffled.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(random() * (index + 1));
    [shuffled[index], shuffled[swapIndex]] = [shuffled[swapIndex], shuffled[index]];
  }

  return shuffled.slice(0, Math.min(capacity, maximumActiveParticles)).map((particle) => particle.id);
}

export function createStarfieldParticlePool(
  width: number,
  height: number,
  exclusion: StarfieldExclusion,
  random: () => number = Math.random,
  baseRadiusMultiplier = 1,
  particleColors: string[] = ['transparent'],
): BackgroundParticle[] {
  const targetCount = Math.min(560, Math.max(280, Math.round((width * height) / 4_000)));
  const particles: BackgroundParticle[] = [];
  let attempts = 0;

  while (particles.length < targetCount && attempts < targetCount * 28) {
    attempts += 1;
    const particle = {
      x: random() * width,
      y: random() * height,
    };
    if (!isOutsideStarfieldExclusion(particle, exclusion)) continue;
    particles.push({
      ...particle,
      id: particles.length,
      radius: (0.45 + (random() * 1.15)) * baseRadiusMultiplier,
      alpha: 0.12 + (random() * 0.24),
      color: particleColors[Math.floor(random() * particleColors.length)],
    });
  }

  return particles;
}

function drawCover(context: CanvasRenderingContext2D, image: HTMLImageElement, width: number, height: number) {
  if (!image.naturalWidth || !image.naturalHeight) return;
  const scale = Math.max(width / image.naturalWidth, height / image.naturalHeight);
  const drawWidth = image.naturalWidth * scale;
  const drawHeight = image.naturalHeight * scale;
  context.drawImage(image, (width - drawWidth) / 2, (height - drawHeight) / 2, drawWidth, drawHeight);
}

function pulseOpacity(progress: number): number {
  if (progress <= 0 || progress >= 1) return 0;
  if (progress < 0.32) return 0.18 + ((progress / 0.32) * 0.78);
  if (progress < 0.58) return 0.96 - (((progress - 0.32) / 0.26) * 0.7);
  if (progress < 0.78) return 0.26 + (((progress - 0.58) / 0.2) * 0.34);
  return 0.6 - (((progress - 0.78) / 0.22) * 0.6);
}

function drawParticle(context: CanvasRenderingContext2D, particle: BackgroundParticle, opacity: number, glowRadiusMultiplier: number) {
  const glow = context.createRadialGradient(particle.x, particle.y, 0, particle.x, particle.y, particle.radius * glowRadiusMultiplier);
  glow.addColorStop(0, particle.color);
  glow.addColorStop(0.2, particle.color);
  glow.addColorStop(1, 'transparent');
  context.globalAlpha = opacity * 0.38;
  context.fillStyle = glow;
  context.beginPath();
  context.arc(particle.x, particle.y, particle.radius * glowRadiusMultiplier, 0, Math.PI * 2);
  context.fill();
  context.globalAlpha = opacity;
  context.fillStyle = particle.color;
  context.beginPath();
  context.arc(particle.x, particle.y, particle.radius, 0, Math.PI * 2);
  context.fill();
}

interface StarmappingStarfieldCanvasProps {
  isFullscreen: boolean;
}

export default function StarmappingStarfieldCanvas({ isFullscreen }: StarmappingStarfieldCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext('2d');
    if (!canvas || !context) return undefined;

    const image = new Image();
    const particleProfile = getStarfieldParticleProfile(isFullscreen);
    const activePulses = new Map<number, ActivePulse>();
    let particles: BackgroundParticle[] = [];
    let particleById = new Map<number, BackgroundParticle>();
    let baseLayer: HTMLCanvasElement | null = null;
    let cssWidth = 0;
    let cssHeight = 0;
    let devicePixelRatio = 1;
    let frameId: number | undefined;
    let intervalId: number | undefined;
    let waveTimeoutId: number | undefined;
    let reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
    let themePalette = resolveStarfieldThemePalette();

    const cancelFrame = () => {
      if (frameId !== undefined) window.cancelAnimationFrame(frameId);
      frameId = undefined;
    };
    const stopSchedule = () => {
      if (intervalId !== undefined) window.clearInterval(intervalId);
      if (waveTimeoutId !== undefined) window.clearTimeout(waveTimeoutId);
      intervalId = undefined;
      waveTimeoutId = undefined;
    };
    const drawBaseLayer = () => {
      if (!baseLayer || !cssWidth || !cssHeight) return;
      const baseContext = baseLayer.getContext('2d');
      if (!baseContext) return;
      baseContext.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
      baseContext.clearRect(0, 0, cssWidth, cssHeight);
      baseContext.fillStyle = themePalette.background;
      baseContext.fillRect(0, 0, cssWidth, cssHeight);
      drawCover(baseContext, image, cssWidth, cssHeight);
      particles.forEach((particle) => drawParticle(baseContext, particle, particle.alpha, particleProfile.glowRadiusMultiplier));
      baseContext.globalAlpha = 1;
    };
    const paint = (timestamp: number) => {
      if (!cssWidth || !cssHeight || !baseLayer) return;
      context.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
      context.clearRect(0, 0, cssWidth, cssHeight);
      context.drawImage(baseLayer, 0, 0, cssWidth, cssHeight);
      activePulses.forEach((pulse, id) => {
        const elapsed = timestamp - pulse.startedAt - pulse.delay;
        const progress = elapsed / pulse.duration;
        if (progress >= 1) {
          activePulses.delete(id);
          return;
        }
        if (progress < 0) return;
        const particle = particleById.get(id);
        if (particle) drawParticle(context, particle, pulseOpacity(progress), particleProfile.glowRadiusMultiplier);
      });
      context.globalAlpha = 1;
    };
    const tick = (timestamp: number) => {
      paint(timestamp);
      if (!reducedMotion && !document.hidden) frameId = window.requestAnimationFrame(tick);
    };
    const startFrameLoop = () => {
      cancelFrame();
      if (!reducedMotion && !document.hidden) frameId = window.requestAnimationFrame(tick);
      else paint(window.performance.now());
    };
    const activateWave = (ids: number[]) => {
      const now = window.performance.now();
      ids.forEach((id) => {
        activePulses.set(id, {
          startedAt: now,
          duration: 1_420 + (Math.random() * 160),
          delay: Math.random() * 220,
        });
      });
    };
    const startPulseBatch = () => {
      if (reducedMotion || document.hidden || particles.length === 0) return;
      const [firstWave, secondWave] = splitParticlePulseWaves(selectParticlePulseBatch(
        particles,
        new Set(activePulses.keys()),
        Math.random,
        particleProfile.maxActiveParticles,
      ));
      activateWave(firstWave);
      waveTimeoutId = window.setTimeout(() => activateWave(secondWave), PULSE_WAVE_STAGGER_MS);
    };
    const startSchedule = () => {
      stopSchedule();
      if (reducedMotion || document.hidden) return;
      startPulseBatch();
      intervalId = window.setInterval(startPulseBatch, PULSE_INTERVAL_MS);
    };
    const resize = () => {
      const bounds = canvas.getBoundingClientRect();
      const nextWidth = Math.round(bounds.width);
      const nextHeight = Math.round(bounds.height);
      if (nextWidth <= 0 || nextHeight <= 0) return;
      cssWidth = nextWidth;
      cssHeight = nextHeight;
      devicePixelRatio = Math.min(window.devicePixelRatio || 1, 2);
      themePalette = resolveStarfieldThemePalette();
      canvas.width = Math.round(cssWidth * devicePixelRatio);
      canvas.height = Math.round(cssHeight * devicePixelRatio);
      baseLayer = document.createElement('canvas');
      baseLayer.width = canvas.width;
      baseLayer.height = canvas.height;
      particles = createStarfieldParticlePool(
        cssWidth,
        cssHeight,
        calculateStarfieldExclusion(cssWidth, cssHeight),
        Math.random,
        particleProfile.baseRadiusMultiplier,
        themePalette.particles,
      );
      particleById = new Map(particles.map((particle) => [particle.id, particle]));
      activePulses.clear();
      drawBaseLayer();
      startSchedule();
      startFrameLoop();
    };
    const onVisibilityChange = () => {
      if (document.hidden) {
        stopSchedule();
        cancelFrame();
        return;
      }
      activePulses.clear();
      startSchedule();
      startFrameLoop();
    };
    const mediaQuery = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    const onReducedMotionChange = (event: MediaQueryListEvent) => {
      reducedMotion = event.matches;
      activePulses.clear();
      startSchedule();
      startFrameLoop();
    };
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(resize);
    const themeObserver = typeof MutationObserver === 'undefined'
      ? undefined
      : new MutationObserver(() => resize());

    const onImageLoad = () => {
      drawBaseLayer();
      paint(window.performance.now());
    };
    image.addEventListener('load', onImageLoad);
    image.src = starfieldBackground;
    observer?.observe(canvas);
    themeObserver?.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-aegis-theme'],
    });
    resize();
    document.addEventListener('visibilitychange', onVisibilityChange);
    mediaQuery?.addEventListener('change', onReducedMotionChange);

    return () => {
      stopSchedule();
      cancelFrame();
      observer?.disconnect();
      themeObserver?.disconnect();
      image.removeEventListener('load', onImageLoad);
      document.removeEventListener('visibilitychange', onVisibilityChange);
      mediaQuery?.removeEventListener('change', onReducedMotionChange);
    };
  }, [isFullscreen]);

  return <canvas ref={canvasRef} data-testid="star-map-background" aria-hidden="true" className="pointer-events-none absolute inset-0 h-full w-full" />;
}
