# One backend microservice for ONE colour (blue or green).
# Rendered by deploy/bluegreen.py for every backend listed in deploy/services.json.
# The colour's ClusterIP Service (${SERVICE}-${COLOR}) is created by Terraform.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${SERVICE}-${COLOR}
  namespace: ${NAMESPACE}
  labels:
    app: ${SERVICE}
    color: ${COLOR}
    version: "${VERSION}"
    app.kubernetes.io/part-of: koalatech
    app.kubernetes.io/managed-by: bluegreen-pipeline
spec:
  replicas: ${REPLICAS}
  revisionHistoryLimit: 2
  selector:
    matchLabels:
      app: ${SERVICE}
      color: ${COLOR}
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  template:
    metadata:
      labels:
        app: ${SERVICE}
        color: ${COLOR}
        version: "${VERSION}"
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8000"
        prometheus.io/path: /metrics
    spec:
      imagePullSecrets:
        - name: ghcr-pull
      terminationGracePeriodSeconds: 20
      containers:
        - name: ${SERVICE}
          image: ${REGISTRY}/${IMAGE}:${VERSION}
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 8000
          envFrom:
            - configMapRef:
                name: koalatech-config
            - secretRef:
                name: koalatech-secrets
          env:
            - name: POSTGRES_HOST
              value: ${DATABASE}-db
            - name: POSTGRES_PORT
              value: "5432"
            - name: POSTGRES_DB
              value: ${DATABASE}
            - name: APP_COLOR
              value: ${COLOR}
            - name: APP_VERSION
              value: "${VERSION}"
            - name: FAULT_RATE
              value: "${FAULT_RATE}"
            - name: FAULT_START_AFTER_SECONDS
              value: "${FAULT_START_AFTER_SECONDS}"
          # Startup allows time for the database retry loop on first boot.
          startupProbe:
            httpGet:
              path: /health
              port: http
            periodSeconds: 2
            failureThreshold: 60
          # Ready only when the database answers, so a pod never receives
          # traffic it cannot serve.
          readinessProbe:
            httpGet:
              path: /ready
              port: http
            periodSeconds: 5
            failureThreshold: 3
          livenessProbe:
            httpGet:
              path: /health
              port: http
            periodSeconds: 10
            failureThreshold: 3
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              memory: 384Mi
          # The images already run as a non-root user (see each Dockerfile).
          securityContext:
            allowPrivilegeEscalation: false
