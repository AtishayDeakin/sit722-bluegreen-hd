# The React frontend (nginx) for ONE colour (blue or green).
# Its nginx only proxies to backends of the same colour (BACKEND_SUFFIX), so a
# colour is a complete, isolated copy of the application stack.
# The public koalatech-prod and koalatech-preview Services select these pods.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: frontend-${COLOR}
  namespace: ${NAMESPACE}
  labels:
    app: frontend
    color: ${COLOR}
    version: "${VERSION}"
    app.kubernetes.io/part-of: koalatech
    app.kubernetes.io/managed-by: bluegreen-pipeline
spec:
  replicas: ${REPLICAS}
  revisionHistoryLimit: 2
  selector:
    matchLabels:
      app: frontend
      color: ${COLOR}
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  template:
    metadata:
      labels:
        app: frontend
        color: ${COLOR}
        version: "${VERSION}"
    spec:
      imagePullSecrets:
        - name: ghcr-pull
      terminationGracePeriodSeconds: 20
      containers:
        - name: frontend
          image: ${REGISTRY}/${IMAGE}:${VERSION}
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 80
          env:
            - name: BACKEND_SUFFIX
              value: -${COLOR}
            - name: APP_COLOR
              value: ${COLOR}
            - name: APP_VERSION
              value: "${VERSION}"
          readinessProbe:
            httpGet:
              path: /healthz
              port: http
            periodSeconds: 5
          livenessProbe:
            httpGet:
              path: /healthz
              port: http
            periodSeconds: 10
          resources:
            requests:
              cpu: 25m
              memory: 32Mi
            limits:
              memory: 128Mi
