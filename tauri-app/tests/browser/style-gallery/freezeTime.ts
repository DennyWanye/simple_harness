// 画廊确定性：固定"现在"与随机数，让相对时间、随机 id 在两次构建里完全相同。
// 必须是画廊入口的第一个导入（早于任何组件模块求值）。
const FIXED_NOW = Date.parse("2026-10-01T08:00:00.000Z");
const RealDate = Date;
class FixedDate extends RealDate {
  constructor(...args: ConstructorParameters<typeof Date> | []) {
    if (args.length === 0) super(FIXED_NOW);
    else super(...(args as ConstructorParameters<typeof Date>));
  }
  static now() {
    return FIXED_NOW;
  }
}
globalThis.Date = FixedDate as DateConstructor;
let seed = 42;
Math.random = () => {
  seed = (seed * 1103515245 + 12345) % 2147483648;
  return seed / 2147483648;
};
performance.now = () => 1000;
export {};
