const PYTHON_API_URL = process.env.PYTHON_API_URL || "http://127.0.0.1:5000";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const upstream = await fetch(`${PYTHON_API_URL}/health`, {
      cache: "no-store"
    });
    const body = await upstream.text();

    return new Response(body, {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") || "application/json"
      }
    });
  } catch (error) {
    return Response.json(
      {
        status: "offline",
        error: error.message
      },
      { status: 503 }
    );
  }
}
