"""Executed only by the owned webots-controller launcher; one Supervisor, serial RPC."""
import hashlib
import json
import os
from pathlib import Path
import socket


def main():
    from controller import Supervisor, Motor, PositionSensor
    e = os.environ
    robot = Supervisor()
    world = Path(robot.getWorldPath()).resolve(strict=True)
    digest = hashlib.sha256(world.read_bytes()).hexdigest()
    if (robot.getName() != e["A04_ROBOT"] or not world.samefile(e["A04_WORLD"])
            or digest != e["A04_HASH"] or not robot.getSupervisor()):
        raise RuntimeError("Supervisor identity mismatch")
    step = robot.getBasicTimeStep()
    if not 0 < step <= 1000 or not step.is_integer():
        raise ValueError("Only integral basicTimeStep in 1..1000ms supported")
    step = int(step)
    completed = 0
    with socket.create_connection(("127.0.0.1", int(e["A04_RPC_PORT"])), timeout=5) as sock:
        sock.settimeout(None)
        with sock.makefile("rwb") as stream:
            def send(data):
                packet = json.dumps(data, allow_nan=False).encode()
                if len(packet) > 60000:
                    raise ValueError("Observation exceeds frame budget")
                stream.write(packet + b"\n")
                stream.flush()
            send(dict(token=e["A04_TOKEN"],pid=os.getpid(), robot=robot.getName(), world=str(world),
                      world_sha256=digest,run_id=e["A04_RUN"],generation=int(e["A04_GENERATION"]),port=int(e["A04_PORT"]),
                      node_id=robot.getSelf().getId(),time=robot.getTime(),basic_time_step_ms=step))
            while raw := stream.readline(65536):
                cmd = json.loads(raw)
                name, a = cmd["name"], cmd["args"]
                data = {}
                if name == "sim_step":
                    for _ in range(a["steps"]):
                        if robot.step(step) == -1:
                            raise RuntimeError("Simulation disconnected")
                        completed += 1
                        send(dict(progress=True, completed_steps=completed, sim_time=robot.getTime()))
                elif name in ("sim_field","sim_contacts"):
                    node = robot.getFromDef(a["node"])
                    if node is None:
                        raise ValueError("Unknown DEF")
                    data = dict(node_id=node.getId(), node_def=a["node"])
                    if name == "sim_contacts":
                        if node.getBaseTypeName() not in ("Solid","Robot"):
                            raise ValueError("Contact scope requires an explicit Solid or Robot")
                        points = node.getContactPoints(False)
                        data["include_descendants"] = False
                        data["position_m"] = node.getPosition()
                        data["velocity_world"] = node.getVelocity()
                        for field_name in ("boundingObject","physics"):
                            field = node.getBaseNodeField(field_name) or node.getField(field_name)
                            field_node = field.getSFNode() if field else None
                            data[field_name] = None if field_node is None else {
                                "type":field_node.getBaseTypeName(),"node_id":field_node.getId()}
                        chain = []
                        parent = node
                        while parent is not None:
                            if len(chain) >= 64:
                                raise ValueError("Unsupported ancestor depth")
                            chain.append({"type":parent.getBaseTypeName(),"node_id":parent.getId()})
                            parent = parent.getParentNode()
                        data["ancestry"] = chain
                        if len(points) > 256:
                            raise ValueError("Contact snapshot exceeds supported count")
                        data["contacts"] = [dict(point=list(p.point), node_id=p.node_id) for p in points]
                        data["physical_verdict"] = "UNKNOWN"
                    else:
                        field = node.getField(a["field"])
                        getters = {"SFFloat":"getSFFloat","SFInt32":"getSFInt32","SFBool":"getSFBool",
                                   "SFVec3f":"getSFVec3f","SFRotation":"getSFRotation","SFString":"getSFString"}
                        if field is None or field.getTypeName() not in getters:
                            raise ValueError("Unsupported field type")
                        data["value"] = getattr(field,getters[field.getTypeName()])()
                elif name == "sim_devices":
                    n = robot.getNumberOfDevices()
                    if n > 128:
                        raise ValueError("Device enumeration exceeds limit")
                    data["devices"] = [robot.getDeviceByIndex(i).getName() for i in range(n)]
                elif name == "sim_motor":
                    device = robot.getDevice(a["device"])
                    if not isinstance(device, Motor):
                        raise ValueError("Expected Motor")
                    device.setPosition(a.get("position", float("inf")))
                    device.setVelocity(a["velocity"])
                    data["feedback_verified"] = False
                elif name == "sim_sensor":
                    sensor = robot.getDevice(a["device"])
                    if not isinstance(sensor, PositionSensor):
                        raise ValueError("Only PositionSensor supported")
                    if sensor.getSamplingPeriod() <= 0:
                        sensor.enable(step)
                    if robot.step(step) == -1:
                        raise RuntimeError("Sensor step disconnected")
                    completed += 1
                    send(dict(progress=True,completed_steps=completed,sim_time=robot.getTime()))
                    units = {PositionSensor.LINEAR:"m",PositionSensor.ROTATIONAL:"rad"}
                    data.update(device=a["device"],value=sensor.getValue(),unit=units[sensor.getType()],
                                device_type="PositionSensor")
                elif name == "sim_scene":
                    data.update(world_sha256=digest,robot=robot.getName(),node_id=robot.getSelf().getId())
                elif name == "sim_screenshot":
                    path = Path(e["A04_OUTPUT"]) / ("capture-" + a["operation_id"] + ".jpg")
                    robot.exportImage(str(path), 90)
                    if robot.step(0) == -1 or not path.is_file() or not 0 < path.stat().st_size <= 4194304:
                        raise RuntimeError("Image export unconfirmed")
                    data.update(image=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                elif name == "sim_shutdown":
                    robot.simulationQuit(0)
                else:
                    raise ValueError("Unsupported operation")
                send(dict(done=True,data=data,sim_time=robot.getTime(),completed_steps=completed))
                if name == "sim_shutdown":
                    return


if __name__ == "__main__":
    main()
