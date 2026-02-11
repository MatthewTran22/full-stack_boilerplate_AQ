import express from "express";
import { readFileSync } from "fs";

const components = JSON.parse(readFileSync(new URL("./components.json", import.meta.url), "utf-8"));
const componentNames = Object.keys(components);

const CSS_VARIABLES = `
:root {
  --background: 0 0% 100%;
  --foreground: 240 10% 3.9%;
  --card: 0 0% 100%;
  --card-foreground: 240 10% 3.9%;
  --popover: 0 0% 100%;
  --popover-foreground: 240 10% 3.9%;
  --primary: 240 5.9% 10%;
  --primary-foreground: 0 0% 98%;
  --secondary: 240 4.8% 95.9%;
  --secondary-foreground: 240 5.9% 10%;
  --muted: 240 4.8% 95.9%;
  --muted-foreground: 240 3.8% 46.1%;
  --accent: 240 4.8% 95.9%;
  --accent-foreground: 240 5.9% 10%;
  --destructive: 0 84.2% 60.2%;
  --destructive-foreground: 0 0% 98%;
  --border: 240 5.9% 90%;
  --input: 240 5.9% 90%;
  --ring: 240 5.9% 10%;
  --radius: 0.5rem;
}
* { border-color: hsl(var(--border)); }
body { background: hsl(var(--background)); color: hsl(var(--foreground)); font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
`;

// Tool definitions in OpenAI function-calling format
const TOOLS = [
  {
    name: "list_components",
    description: "List all available shadcn/ui components",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "get_component",
    description: "Get the HTML pattern, classes, and usage for a specific shadcn/ui component",
    inputSchema: {
      type: "object",
      properties: {
        name: { type: "string", description: `Component name. One of: ${componentNames.join(", ")}` },
      },
      required: ["name"],
    },
  },
  {
    name: "get_components",
    description: "Get HTML patterns for multiple shadcn/ui components at once",
    inputSchema: {
      type: "object",
      properties: {
        names: { type: "array", items: { type: "string" }, description: "Array of component names" },
      },
      required: ["names"],
    },
  },
  {
    name: "get_design_tokens",
    description: "Get the shadcn/ui CSS variables and design tokens to include in the HTML <style> block",
    inputSchema: { type: "object", properties: {} },
  },
];

// Tool handlers
function handleTool(name, args) {
  switch (name) {
    case "list_components": {
      const list = componentNames.map((key) => ({
        name: components[key].name,
        key,
        description: components[key].description,
      }));
      return { content: [{ type: "text", text: JSON.stringify(list, null, 2) }] };
    }

    case "get_component": {
      const key = (args.name || "").toLowerCase().replace(/[\s-]/g, "_");
      const comp = components[key];
      if (!comp) {
        return {
          content: [{ type: "text", text: `Component "${args.name}" not found. Available: ${componentNames.join(", ")}` }],
          isError: true,
        };
      }
      return { content: [{ type: "text", text: JSON.stringify(comp, null, 2) }] };
    }

    case "get_components": {
      const results = {};
      for (const n of args.names || []) {
        const key = n.toLowerCase().replace(/[\s-]/g, "_");
        if (components[key]) results[key] = components[key];
      }
      return { content: [{ type: "text", text: JSON.stringify(results, null, 2) }] };
    }

    case "get_design_tokens":
      return { content: [{ type: "text", text: CSS_VARIABLES }] };

    default:
      return { content: [{ type: "text", text: `Unknown tool: ${name}` }], isError: true };
  }
}

const app = express();
app.use(express.json());

// MCP JSON-RPC endpoint
app.post("/mcp", (req, res) => {
  const { method, params, id } = req.body;
  console.log(`MCP request: ${method}`, params ? JSON.stringify(params).slice(0, 200) : "");

  let result;
  switch (method) {
    case "initialize":
      result = {
        protocolVersion: "2025-03-26",
        capabilities: { tools: {} },
        serverInfo: { name: "shadcn-ui", version: "1.0.0" },
      };
      break;

    case "tools/list":
      result = { tools: TOOLS };
      break;

    case "tools/call":
      result = handleTool(params.name, params.arguments || {});
      break;

    default:
      res.json({ jsonrpc: "2.0", id, error: { code: -32601, message: `Unknown method: ${method}` } });
      return;
  }

  res.json({ jsonrpc: "2.0", id, result });
});

// Health check
app.get("/health", (req, res) => {
  res.json({ status: "ok", tools: TOOLS.length, components: componentNames.length });
});

const PORT = process.env.PORT || 8001;
app.listen(PORT, "0.0.0.0", () => {
  console.log(`shadcn MCP server running on port ${PORT}`);
  console.log(`Tools: ${TOOLS.map((t) => t.name).join(", ")}`);
  console.log(`Components: ${componentNames.length}`);
});
