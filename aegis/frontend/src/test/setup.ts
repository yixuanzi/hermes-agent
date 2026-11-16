import '@testing-library/jest-dom/vitest';

const canvasGradient = { addColorStop: () => undefined };
const canvasContext = {
  arc: () => undefined,
  beginPath: () => undefined,
  clearRect: () => undefined,
  createRadialGradient: () => canvasGradient,
  drawImage: () => undefined,
  fill: () => undefined,
  fillRect: () => undefined,
  setTransform: () => undefined,
};

Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', {
  configurable: true,
  value: () => canvasContext as unknown as CanvasRenderingContext2D,
});
