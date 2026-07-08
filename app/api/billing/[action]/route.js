const PYTHON_API_URL = process.env.PYTHON_API_URL || "http://127.0.0.1:5000";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const ALLOWED_ACTIONS = new Set(["plans", "status", "checkout"]);

async function proxyBilling(request, { params }) {
  const { action } = await params;
  if (!ALLOWED_ACTIONS.has(action)) {
    return Response.json({ error: "Неизвестное действие." }, { status: 404 });
  }

  try {
    const authorization = request.headers.get("authorization");
    const headers = {
      "content-type": request.headers.get("content-type") || "application/json"
    };
    if (authorization) headers.authorization = authorization;

    const method = request.method;
    const hasBody = method !== "GET" && method !== "HEAD";
    const upstream = await fetch(`${PYTHON_API_URL}/api/billing/${action}`, {
      method,
      headers,
      body: hasBody ? await request.text() : undefined,
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
        error: `Сервер тарифов недоступен: ${error.message}`
      },
      { status: 503 }
    );
  }
}

export async function GET(request, context) {
  return proxyBilling(request, context);
}

export async function POST(request, context) {
  return proxyBilling(request, context);
}

export async function OPTIONS() {
  return new Response(null, { status: 204 });
}
