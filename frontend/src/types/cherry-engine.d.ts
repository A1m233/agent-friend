declare module "cherry-markdown/dist/cherry-markdown.engine.core.esm.js" {
  export default class CherryEngine {
    constructor(options: object);
    makeHtml(markdown: string): string;
  }
}
