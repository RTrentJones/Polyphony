/** @type {import('next').NextConfig} */
// Static export: FastAPI serves the built site same-origin in the container
// (see Dockerfile), so /api/v1 needs no rewrite there. `npm run dev` keeps a
// rewrite to the local backend for the dev loop.
const isDev = process.env.NODE_ENV === 'development'

const nextConfig = {
  output: 'export',
  ...(isDev
    ? {
        async rewrites() {
          return [
            {
              source: '/api/:path*',
              destination: `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/:path*`,
            },
          ]
        },
      }
    : {}),
}

module.exports = nextConfig
