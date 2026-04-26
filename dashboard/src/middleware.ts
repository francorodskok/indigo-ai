// middleware.ts — basic auth para /admin/* y /api/social/*.
//
// Si la env var `DASHBOARD_ADMIN_PASSWORD` no está seteada, el middleware
// deja pasar todo (modo dev). En prod (Vercel), seteás la env var y queda
// protegido con HTTP basic auth (el browser pide user/pass).
//
// Username: cualquiera (por convención `franco`). Sólo se valida la pass.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export function middleware(req: NextRequest) {
  const expected = process.env.DASHBOARD_ADMIN_PASSWORD;
  if (!expected) return NextResponse.next(); // sin password → dev mode, libre

  const auth = req.headers.get("authorization") ?? "";
  if (auth.startsWith("Basic ")) {
    try {
      const decoded = Buffer.from(auth.slice(6), "base64").toString("utf8");
      const [, password] = decoded.split(":", 2);
      if (password === expected) return NextResponse.next();
    } catch {
      // fall-through al 401
    }
  }
  return new NextResponse("Auth required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="indigo-admin"' },
  });
}

// Aplicar solo a las rutas sensibles. El resto del dashboard sigue siendo
// público.
export const config = {
  matcher: ["/admin/:path*", "/api/social/:path*"],
};
