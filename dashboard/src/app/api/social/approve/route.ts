// POST /api/social/approve
// Body: { fileName: "post_2026-04-26_didactico.json" }
// Mueve el draft de pipeline/outputs/social/drafts/ a approved/.
//
// Auth: protegido por el middleware /admin → la cookie/header básica ya pasó.
// Igual chequeo Authorization Basic header acá por si el middleware no
// alcanza por algún motivo.

import { NextResponse } from "next/server";
import { approveDraft } from "@/lib/social";
import { revalidatePath } from "next/cache";

export const dynamic = "force-dynamic";

function checkBasicAuth(req: Request): boolean {
  const expected = process.env.DASHBOARD_ADMIN_PASSWORD;
  if (!expected) return true; // dev mode sin password seteada
  const auth = req.headers.get("authorization") ?? "";
  if (!auth.startsWith("Basic ")) return false;
  try {
    const decoded = Buffer.from(auth.slice(6), "base64").toString("utf8");
    const [, password] = decoded.split(":", 2);
    return password === expected;
  } catch {
    return false;
  }
}

export async function POST(req: Request) {
  if (!checkBasicAuth(req)) {
    return NextResponse.json(
      { ok: false, error: "unauthorized" },
      {
        status: 401,
        headers: { "WWW-Authenticate": 'Basic realm="indigo-admin"' },
      },
    );
  }

  let body: { fileName?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json(
      { ok: false, error: "body inválido (esperaba JSON)" },
      { status: 400 },
    );
  }
  const fileName = typeof body?.fileName === "string" ? body.fileName : null;
  if (!fileName) {
    return NextResponse.json(
      { ok: false, error: "fileName (string) requerido" },
      { status: 400 },
    );
  }

  const result = await approveDraft(fileName);
  if (!result.ok) {
    return NextResponse.json(result, { status: 400 });
  }

  // Invalidar caches de la página para que el draft aprobado aparezca al toque.
  try {
    revalidatePath("/admin/social");
  } catch {
    // revalidatePath puede fallar fuera de un context Next; ignorar.
  }

  return NextResponse.json({ ok: true });
}
