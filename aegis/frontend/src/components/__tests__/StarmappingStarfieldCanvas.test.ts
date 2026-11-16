import { describe, expect, it } from 'vitest';

import {
  calculateStarfieldExclusion,
  createStarfieldParticlePool,
  getStarfieldParticleProfile,
  isOutsideStarfieldExclusion,
  selectParticlePulseBatch,
  splitParticlePulseWaves,
} from '../StarmappingStarfieldCanvas';

function seededRandom(seed = 17) {
  let value = seed;
  return () => {
    value = (value * 1_103_515_245 + 12_345) % 2_147_483_648;
    return value / 2_147_483_648;
  };
}

describe('StarmappingStarfieldCanvas helpers', () => {
  it('keeps generated particle candidates outside the complete foreground constellation envelope', () => {
    const exclusion = calculateStarfieldExclusion(1000, 700);
    const particles = createStarfieldParticlePool(1000, 700, exclusion, seededRandom());

    expect(isOutsideStarfieldExclusion({ x: exclusion.x, y: exclusion.y }, exclusion)).toBe(false);
    expect(isOutsideStarfieldExclusion({ x: exclusion.x + exclusion.radius + 1, y: exclusion.y }, exclusion)).toBe(true);
    expect(particles).toHaveLength(280);
    expect(particles.every((particle) => isOutsideStarfieldExclusion(particle, exclusion))).toBe(true);
  });

  it('applies the smaller inline particle profile while keeping fullscreen unchanged', () => {
    const particles = Array.from({ length: 180 }, (_, id) => ({ id, x: id, y: id, alpha: 0.2, color: '#75d7ee', radius: 1 }));
    const inlineProfile = getStarfieldParticleProfile(false);
    const fullscreenProfile = getStarfieldParticleProfile(true);
    const initialBatch = selectParticlePulseBatch(particles, new Set(), () => 0.25, inlineProfile.maxActiveParticles);
    const [initialWave, delayedWave] = splitParticlePulseWaves(initialBatch);
    const handoffBatch = selectParticlePulseBatch(particles, new Set(delayedWave), () => 0.25, inlineProfile.maxActiveParticles);
    const [handoffWave, handoffDelayedWave] = splitParticlePulseWaves(handoffBatch);

    expect(inlineProfile).toEqual({ baseRadiusMultiplier: 0.5, glowRadiusMultiplier: 3, maxActiveParticles: 50 });
    expect(fullscreenProfile).toEqual({ baseRadiusMultiplier: 1, glowRadiusMultiplier: 7, maxActiveParticles: 100 });
    expect(initialBatch).toHaveLength(50);
    expect(initialWave).toHaveLength(25);
    expect(delayedWave).toHaveLength(25);
    expect(handoffBatch).toHaveLength(25);
    expect(handoffWave).toHaveLength(13);
    expect(handoffDelayedWave).toHaveLength(12);
    expect(handoffBatch.some((id) => delayedWave.includes(id))).toBe(false);
  });
});
