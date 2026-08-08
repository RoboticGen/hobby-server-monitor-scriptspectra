# Phase 1 Learning Notes — Understanding LXD Integration

> Personal learning notes while implementing Phase 1 of the Hobby Server Monitor.
>
> These notes document my understanding of the concepts behind the code,
> not just what the code does.

---

# 1. What is LXD?

LXD is the container management system used by the Hobby Server Monitor.

The application communicates with LXD to discover and manage containers.

The important idea is:

    Browser
       ↓
    Astro Dashboard
       ↓
    Falcon Backend
       ↓
    pylxd
       ↓
    LXD
       ↓
    Containers

The browser does NOT directly communicate with LXD.

The backend acts as the controlled interface between the web application
and the LXD daemon.

---

# 2. Why does the browser not communicate directly with LXD?

The backend provides a security and authorization boundary.

For example, when a user clicks "Start Container":

    User
      ↓
    Dashboard
      ↓
    HTTP request
      ↓
    Falcon backend
      ↓
    Authentication / Authorization
      ↓
    pylxd
      ↓
    LXD
      ↓
    Container starts

This means the backend can decide whether the user is allowed to perform
the requested operation before talking to LXD.

---

# 3. What is LXD_ENDPOINT?

I can think of:

    LXD_ENDPOINT = "Where is LXD?"

It tells the application where the LXD daemon/API can be reached.

There are two main possibilities.

## Local LXD

If:

    LXD_ENDPOINT=""

the application uses the local LXD connection.

The `pylxd.Client()` call connects to LXD through a Unix socket.

Architecture:

    Backend
       ↓
    Unix Socket
       ↓
    Local LXD
       ↓
    Containers

---

## Remote LXD

If:

    LXD_ENDPOINT=https://server:8443

the backend can communicate with an LXD API running on another machine.

Architecture:

    Machine A
    ┌──────────────────┐
    │ Dashboard        │
    │ Backend          │
    │ pylxd            │
    └────────┬─────────┘
             │
             │ HTTPS
             ↓
    Machine B
    ┌──────────────────┐
    │ LXD              │
    │                  │
    │ Container 1      │
    │ Container 2      │
    │ Container 3      │
    └──────────────────┘

This is conceptually similar to RPC because the backend is requesting
operations from a service running on another machine, although LXD's
remote API uses HTTP(S).

---

# 4. What is a Unix socket?

A Unix socket is a local communication mechanism provided by the OS.

It allows two processes on the same machine to communicate.

Instead of:

    Backend → Network → LXD

the communication can be:

    Backend → Unix Socket → LXD

This is why a Unix socket is useful when the backend and LXD are running
on the same server.

The backend does not need to expose LXD's API directly to the network
just to communicate with the local daemon.

---

# 5. Why support both local and remote LXD?

This is mainly about deployment flexibility.

It is NOT necessarily an "admin machine vs user machine" distinction.

The application could be deployed like:

    Same machine:

    Backend
       ↓
    Local LXD
       ↓
    Containers

or:

    Separate machines:

    Backend
       ↓
    Remote LXD
       ↓
    Containers

The application can therefore support different deployment
architectures.

---

# 6. Admin vs Users is a different concept

I initially thought local vs remote LXD might mean:

    Local = admin
    Remote = users

But these are actually separate concerns.

Where LXD is located is a deployment/connection concern.

Who is allowed to use the application is an authentication and
authorization concern.

For example:

    User A ──┐
    User B ──┼──→ Dashboard → Backend → LXD
    Admin ───┘

The backend can determine what each user is allowed to do.

Example:

    Normal user:
        View containers       ✓
        Start/stop            Maybe
        Assign containers     ✗

    Admin:
        View containers       ✓
        Start/stop            ✓
        Assign containers     ✓

So:

    LXD_ENDPOINT
        ↓
    "Where is LXD?"

while:

    Authentication
        ↓
    "Who is this user?"

and:

    Authorization
        ↓
    "What can this user do?"

These are different concepts.

---

# 7. What is pylxd?

`pylxd` is the Python library used by the backend to communicate
with LXD.

Instead of manually constructing LXD API requests, Python code can
use the `pylxd` API.

For example:

    client = get_client()

Then:

    client.containers.all

represents an LXD operation for retrieving containers.

---

# 8. What is the pylxd Client?

The application creates a `pylxd.Client`.

The client represents the application's connection/interface to LXD.

The project uses a module-level singleton.

The important variable is:

    _client

Initially:

    _client = None

When `get_client()` is called for the first time, the client is created.

After that, subsequent calls reuse the same client.

Conceptually:

    First call:
        get_client()
            ↓
        create pylxd.Client
            ↓
        store in _client

    Later call:
        get_client()
            ↓
        return existing _client

This avoids repeatedly creating client objects.

---

# 9. Understanding get_client()

The important logic is:

    if _client is None:
        endpoint = settings.LXD_ENDPOINT.strip() or None

        if endpoint:
            _client = pylxd.Client(endpoint=endpoint)
        else:
            _client = pylxd.Client()

    return _client

The logic is:

    Is a client already available?
             │
       ┌─────┴─────┐
       │           │
      YES          NO
       │           │
    return it    Read LXD_ENDPOINT
                       │
                ┌──────┴──────┐
                │             │
             endpoint       empty
                │             │
                ↓             ↓
        Remote LXD       Local LXD
        pylxd.Client     pylxd.Client()
                │             │
                └──────┬──────┘
                       ↓
                   _client
                       ↓
                    return

---

# 10. What is reset_client()?

`reset_client()` does:

    _client = None

It discards the existing client.

The next call to:

    get_client()

will therefore create a fresh client.

This can be useful if the existing connection is known to be broken.

---

# 11. What is lxd_safe()?

This was an important realization.

`lxd_safe()` itself does NOT know how to retrieve information from LXD.

It is a safety wrapper around an LXD operation.

I can think of it as:

    "Run this LXD operation, and if LXD has a communication
     problem, don't crash the application."

The actual function passed to `lxd_safe()` determines what operation
is performed.

---

# 12. What kind of function do we give lxd_safe()?

We give it a callable.

For example:

    client = get_client()

    containers, err = lxd_safe(client.containers.all)

Here:

    client.containers.all
             ↑
       actual LXD operation

It means:

    "Get all containers from LXD."

`lxd_safe()` executes that operation safely.

---

# 13. What happens when the operation succeeds?

For example:

    containers, err = lxd_safe(client.containers.all)

If the operation succeeds:

    containers = [...]
    err = None

Then I can use the result:

    for container in containers:
        print(container.name)
        print(container.status)

So the returned tuple is:

    (result, None)

---

# 14. Can lxd_safe() perform different operations?

Yes.

The operation passed to it determines what happens.

Examples:

    lxd_safe(client.containers.all)

    lxd_safe(
        client.containers.get,
        "my-container"
    )

    lxd_safe(container.state)

    lxd_safe(container.start)

    lxd_safe(container.stop)

The wrapper itself doesn't decide what information to retrieve.

The caller decides the operation.

---

# 15. Understanding the pattern

The pattern is:

                     lxd_safe()
                         │
               ┌─────────┴─────────┐
               │                   │
        containers.all       container.state
               │                   │
               ↓                   ↓
          LXD operation       LXD operation

So:

    lxd_safe()
        =
    safety/error-handling layer

while:

    client.containers.all
        =
    actual LXD operation

---

# 16. What does lxd_safe() return?

It returns:

    (result, error)

On success:

    (result, None)

For example:

    containers, err = lxd_safe(client.containers.all)

might produce:

    containers = [container1, container2]
    err = None

If an LXD communication error occurs:

    (None, "some error message")

So the caller can do:

    containers, err = lxd_safe(client.containers.all)

    if err:
        log.warning("LXD unavailable: %s", err)
    else:
        for container in containers:
            print(container.name)

---

# 17. Why do we need lxd_safe()?

Without a common wrapper, every part of the application might need
its own LXD error handling.

For example:

    try:
        containers = client.containers.all
    except ...:
        ...

and somewhere else:

    try:
        state = container.state
    except ...:
        ...

Instead, the project centralizes the common LXD communication error
handling:

    containers, err = lxd_safe(client.containers.all)

    state, err = lxd_safe(container.state)

This keeps the calling code simpler and gives the application a
consistent way of handling LXD communication failures.

---

# 18. What errors does lxd_safe() handle?

It catches known LXD/socket-related failures such as:

    pylxd.exceptions.LXDAPIException
    pylxd.exceptions.NotFound
    ConnectionRefusedError
    ConnectionResetError
    TimeoutError
    FileNotFoundError
    OSError
    BrokenPipeError

These can represent problems communicating with LXD.

Instead of allowing these expected communication failures to crash
the caller, `lxd_safe()` converts them into:

    (None, error_message)

and logs the problem.

---

# 19. What about unexpected programming errors?

`lxd_safe()` does NOT silently swallow every possible exception.

There is another branch:

    except Exception as exc:
        ...
        raise

This is important.

If there is an unexpected programming error, it gets logged and
re-raised.

That means the wrapper is primarily protecting against expected
LXD/communication failures rather than hiding bugs in the application.

---

# 20. Does lxd_safe() retry?

No.

This is another important design decision.

`lxd_safe()` does NOT implement retry logic.

Its job is:

    Execute operation
          ↓
    Catch known LXD failure
          ↓
    Return error

If another component needs retries, that component should implement
the retry policy.

For example, a future collector might do:

    LXD request
        ↓
      failed
        ↓
      wait
        ↓
      retry
        ↓
      retry again
        ↓
    mark LXD unavailable

The retry policy belongs to the collector, not `lxd_safe()`.

---

# 21. Big Picture Understanding

The complete relationship is:

    Browser
       │
       │ HTTP
       ↓
    Astro Dashboard
       │
       │ HTTP/API
       ↓
    Falcon Backend
       │
       │ Python
       ↓
    lxd_client.py
       │
       │ pylxd
       ↓
    ┌─────────────────────┐
    │        LXD          │
    └─────────────────────┘
       │
       ├── Container 1
       ├── Container 2
       └── Container 3

`LXD_ENDPOINT` answers:

    "Where is LXD?"

`get_client()` answers:

    "Give the backend a pylxd client for that LXD."

`lxd_safe()` answers:

    "Execute this LXD operation safely."

The actual operation answers:

    "What do I want to do with LXD?"

For example:

    client.containers.all
        → get containers

    container.state
        → get container state

    container.start
        → start container

    container.stop
        → stop container

---

# 22. My Main Takeaway

I initially thought:

    lxd_safe()
        =
    "function that gets information from LXD"

My corrected understanding is:


    pylxd
        =
    Python interface to LXD

    client.containers.all
        =
    actual operation/request

    lxd_safe(...)
        =
    safe error-handling wrapper around that operation

So:

    containers, err = lxd_safe(client.containers.all)

means:

    "Use the LXD client to request all containers,
     but handle known LXD communication failures safely."

This distinction is important for understanding how the API and
collector phases will use `lxd_client.py`.
