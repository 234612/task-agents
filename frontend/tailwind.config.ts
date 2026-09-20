import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    // 确保包含 ./src 路径，这样 Tailwind 才能扫描到组件里的 class
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {},
  },
  plugins: [],
};

export default config;