declare module 'plotly.js-dist-min' {
  export interface PlotlyModule {
    newPlot(
      root: HTMLElement,
      data: unknown[],
      layout?: Record<string, unknown>,
      config?: Record<string, unknown>,
    ): Promise<void>
  }
  const Plotly: PlotlyModule
  export default Plotly
}
