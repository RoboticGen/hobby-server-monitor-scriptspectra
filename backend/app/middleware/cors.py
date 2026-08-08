"""
backend/app/middleware/cors.py

CORS Middleware for Astro dev server support.
Allows credentials (cookies) and requests from Astro dev server (localhost:4321).
"""

class CORSMiddleware:
    def process_request(self, req, resp):
        # Set CORS headers
        resp.set_header("Access-Control-Allow-Origin", "http://localhost:4321")
        resp.set_header("Access-Control-Allow-Credentials", "true")
        resp.set_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        resp.set_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def process_response(self, req, resp, resource, req_succeeded):
        # Handle pre-flight OPTIONS requests
        if req.method == "OPTIONS":
            resp.status = "200 OK"
