/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  experimental: {
    // 启用路径别名支持，解决 @/xxx 引用报错的问题
    appDir: true,
  },
  webpack(config) {
    // 确保 webpack 解析 @ 符号到 src 目录
    config.resolve.alias['@'] = './src';
    return config;
  },
};

export default nextConfig;