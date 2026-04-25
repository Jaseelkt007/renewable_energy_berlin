/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // three.js + R3F use ESM and `three/examples/jsm/...` paths.
  // Next 14's transpilePackages keeps them happy in App Router.
  transpilePackages: ["three"],
};

module.exports = nextConfig;
