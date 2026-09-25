import type { Config } from 'tailwindcss';

/**
 * OpenCode 终端风格设计令牌（见 design-md/opencode.ai/DESIGN.md）：
 * 全站等宽字体、奶油画布 + 近黑墨色、1px 发丝线代替阴影、
 * 4px 圆角仅用于可交互元素、语义色沿用 Apple HIG 渐变。
 */
const monoStack = [
  'JetBrains Mono',
  'IBM Plex Mono',
  'ui-monospace',
  'SFMono-Regular',
  'Menlo',
  'Monaco',
  'Consolas',
  'Liberation Mono',
  'Courier New',
  'monospace',
];

const config: Config = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        canvas: '#fdfcfc',
        'surface-soft': '#f8f7f7',
        'surface-card': '#f1eeee',
        ink: '#201d1d',
        'ink-deep': '#0f0000',
        charcoal: '#302c2c',
        'surface-dark': '#201d1d',
        'surface-dark-elevated': '#302c2c',
        body: '#424245',
        mute: '#646262',
        stone: '#6e6e73',
        ash: '#9a9898',
        hairline: 'rgba(15,0,0,0.12)',
        'hairline-strong': '#646262',
        'on-dark': '#fdfcfc',
        'on-dark-mute': '#9a9898',
        accent: '#007aff',
        'accent-hover': '#0056b3',
        'accent-active': '#004085',
        warning: '#ff9f0a',
        'warning-hover': '#cc7f08',
        'warning-active': '#995f06',
        danger: '#ff3b30',
        'danger-hover': '#d70015',
        'danger-active': '#a50011',
        success: '#30d158',
      },
      // 全站只允许 0 / 4px / 9999px 三档圆角；sans 指向等宽栈，
      // 使 Preflight 默认的 body 字体即为等宽，无需逐个组件声明。
      borderRadius: {
        none: '0px',
        sm: '4px',
        full: '9999px',
      },
      fontFamily: {
        sans: monoStack,
        mono: monoStack,
      },
    },
  },
  plugins: [],
};

export default config;