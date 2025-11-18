# Polyphony Frontend

Next.js frontend for the Polyphony multi-character creative writing platform.

## Features

- **Authentication**: Secure JWT-based authentication with login/register
- **Manuscript Management**: Upload, view, and manage manuscripts
- **Character Analysis**: View extracted characters with traits and dialogue patterns
- **Scene Generation**: AI-powered scene generation with character-specific dialogue
- **Real-time Updates**: Live status updates for manuscript processing
- **Responsive Design**: Mobile-first design with Tailwind CSS

## Tech Stack

- **Framework**: Next.js 15 (App Router)
- **Language**: TypeScript
- **Styling**: Tailwind CSS
- **State Management**: Zustand
- **HTTP Client**: Axios
- **Animations**: Framer Motion
- **Icons**: Lucide React

## Getting Started

### Prerequisites

- Node.js 18+ and npm
- Running Polyphony backend API (default: http://localhost:8000)

### Installation

```bash
# Install dependencies
npm install

# Copy environment variables
cp .env.local.example .env.local

# Edit .env.local with your API URL
# NEXT_PUBLIC_API_URL=http://localhost:8000
```

### Development

```bash
# Run development server
npm run dev

# Open http://localhost:3000
```

### Build

```bash
# Build for production
npm run build

# Start production server
npm start
```

## Project Structure

```
frontend/
├── app/                    # Next.js app directory
│   ├── auth/              # Authentication pages
│   ├── dashboard/         # Dashboard page
│   ├── manuscripts/       # Manuscript management
│   ├── generate/          # Scene generation
│   ├── layout.tsx         # Root layout
│   ├── page.tsx           # Home page (redirects)
│   ├── error.tsx          # Error boundary
│   └── not-found.tsx      # 404 page
├── components/            # Reusable components
│   ├── Button.tsx
│   ├── Input.tsx
│   ├── Card.tsx
│   ├── Modal.tsx
│   ├── Loading.tsx
│   ├── Toast.tsx
│   ├── FileUpload.tsx
│   ├── Navbar.tsx
│   └── ProtectedRoute.tsx
├── lib/                   # Utilities and configuration
│   ├── api-client.ts     # API client
│   ├── store.ts          # Zustand stores
│   ├── types.ts          # TypeScript types
│   └── utils.ts          # Utility functions
├── public/               # Static assets
└── package.json
```

## Environment Variables

- `NEXT_PUBLIC_API_URL`: Backend API URL (default: http://localhost:8000)

## Features in Detail

### Authentication
- Secure login and registration
- JWT token management
- Protected routes with automatic redirect
- Persistent authentication state

### Manuscript Management
- Upload manuscripts (TXT, DOC, DOCX, PDF)
- View processing status
- Browse uploaded manuscripts
- View character extraction results

### Scene Generation
- Select manuscript and characters
- Configure scene parameters (setting, tone, word count)
- Generate character-driven dialogue
- View generated scenes

### UI Components
- Consistent design system
- Accessible form components
- Loading states and error handling
- Toast notifications
- Modal dialogs
- File upload with drag & drop

## API Integration

The frontend communicates with the Polyphony backend API:

- `POST /api/v1/auth/register` - Register new user
- `POST /api/v1/auth/login` - Login user
- `GET /api/v1/auth/me` - Get current user
- `POST /api/v1/manuscripts/upload` - Upload manuscript
- `GET /api/v1/manuscripts/` - List manuscripts
- `GET /api/v1/manuscripts/{id}` - Get manuscript details
- `GET /api/v1/manuscripts/{id}/characters` - Get characters
- `POST /api/v1/scenes/generate` - Generate scene
- `GET /api/v1/scenes/` - List scenes

## Development Notes

- All routes under `/dashboard`, `/manuscripts`, and `/generate` are protected
- Authentication state is managed with Zustand
- API calls are handled through a centralized API client
- Error handling is implemented at both component and global levels

## License

Part of the Polyphony project.
