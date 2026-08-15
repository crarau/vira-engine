import type { NextConfig } from "next";

const config: NextConfig = {
  // This is a local inspection tool. Nothing is prerendered against a live API,
  // every page fetches in the browser, so there is no build-time API dependency.
  reactStrictMode: true,
};

export default config;
