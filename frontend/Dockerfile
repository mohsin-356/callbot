# Frontend (React)
FROM node:20-alpine

WORKDIR /app

# Install dependencies (use npm ci if lockfile exists for reproducible builds)
COPY package*.json ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# Copy source and build
COPY . .
RUN npm run build

EXPOSE 3000

# Serve the production build using Vite preview
CMD ["npm", "run", "preview", "--", "--host", "0.0.0.0", "--port", "3000"]
