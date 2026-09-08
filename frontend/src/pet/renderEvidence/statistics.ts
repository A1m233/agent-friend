export class RenderStatistics {
  ticks = 0;
  renders = 0;
  timerTicks = 0;
  maxRenderMs = 0;
  maxTickGapMs = 0;
  private previousTick: number | null = null;
  private renderStarted: number | null = null;
  tick(now: number) {
    this.ticks++;
    if (this.previousTick !== null) this.maxTickGapMs = Math.max(this.maxTickGapMs, now - this.previousTick);
    this.previousTick = now;
  }
  begin(now: number) { this.renderStarted = now; }
  end(now: number, toScreen: boolean) {
    if (toScreen) {
      this.renders++;
      if (this.renderStarted !== null) this.maxRenderMs = Math.max(this.maxRenderMs, now - this.renderStarted);
    }
    this.renderStarted = null;
  }
  take() {
    const snapshot = { ticks: this.ticks, renders: this.renders, timerTicks: this.timerTicks,
      maxRenderMs: this.maxRenderMs, maxTickGapMs: this.maxTickGapMs };
    this.ticks = this.renders = this.timerTicks = this.maxRenderMs = this.maxTickGapMs = 0;
    return snapshot;
  }
}
