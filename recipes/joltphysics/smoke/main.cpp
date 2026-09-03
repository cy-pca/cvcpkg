// recipes/joltphysics/smoke/main.cpp — drop a sphere on a floor and check it
// comes to rest.  Structure follows Jolt's HelloWorld sample.
//
// Deliberately uses JobSystemSingleThreaded: in a non-pthread Emscripten build
// std::thread's constructor throws std::system_error("Not supported") at
// runtime (verified with emsdk 5.0.7 / node 22), so JobSystemThreadPool — which
// spawns std::threads in its constructor — is unusable there.  Natively the
// single-threaded system is also the reproducible choice for a smoke test.
#include <Jolt/Jolt.h>

#include <Jolt/Core/Factory.h>
#include <Jolt/Core/JobSystemSingleThreaded.h>
#include <Jolt/Core/TempAllocator.h>
#include <Jolt/Physics/Body/BodyCreationSettings.h>
#include <Jolt/Physics/Body/BodyInterface.h>
#include <Jolt/Physics/Collision/Shape/BoxShape.h>
#include <Jolt/Physics/Collision/Shape/SphereShape.h>
#include <Jolt/Physics/PhysicsSettings.h>
#include <Jolt/Physics/PhysicsSystem.h>
#include <Jolt/RegisterTypes.h>

#include <cmath>
#include <cstdio>

// JPH_VERSION_ID expands to a cast through an unqualified `uint64`, which only
// resolves inside namespace JPH; pull the alias in so the macro works here.
using JPH::uint64;

namespace Layers {
static constexpr JPH::ObjectLayer NON_MOVING = 0;
static constexpr JPH::ObjectLayer MOVING = 1;
static constexpr JPH::ObjectLayer NUM_LAYERS = 2;
} // namespace Layers

class ObjectLayerPairFilterImpl final : public JPH::ObjectLayerPairFilter {
public:
  bool ShouldCollide(JPH::ObjectLayer a, JPH::ObjectLayer b) const override {
    if (a == Layers::NON_MOVING) return b == Layers::MOVING;
    return true;
  }
};

namespace BroadPhaseLayers {
static constexpr JPH::BroadPhaseLayer NON_MOVING(0);
static constexpr JPH::BroadPhaseLayer MOVING(1);
static constexpr JPH::uint NUM_LAYERS(2);
} // namespace BroadPhaseLayers

class BPLayerInterfaceImpl final : public JPH::BroadPhaseLayerInterface {
public:
  JPH::uint GetNumBroadPhaseLayers() const override { return BroadPhaseLayers::NUM_LAYERS; }
  JPH::BroadPhaseLayer GetBroadPhaseLayer(JPH::ObjectLayer layer) const override {
    return layer == Layers::NON_MOVING ? BroadPhaseLayers::NON_MOVING : BroadPhaseLayers::MOVING;
  }
#if defined(JPH_EXTERNAL_PROFILE) || defined(JPH_PROFILE_ENABLED)
  const char *GetBroadPhaseLayerName(JPH::BroadPhaseLayer layer) const override {
    return layer == BroadPhaseLayers::NON_MOVING ? "NON_MOVING" : "MOVING";
  }
#endif
};

class ObjectVsBroadPhaseLayerFilterImpl final : public JPH::ObjectVsBroadPhaseLayerFilter {
public:
  bool ShouldCollide(JPH::ObjectLayer layer, JPH::BroadPhaseLayer bp) const override {
    if (layer == Layers::NON_MOVING) return bp == BroadPhaseLayers::MOVING;
    return true;
  }
};

int main() {
  JPH::RegisterDefaultAllocator();
  JPH::Factory::sInstance = new JPH::Factory();
  // Aborts the process on a library/consumer JPH_* mismatch (Jolt/RegisterTypes.cpp).
  JPH::RegisterTypes();
  std::printf("jolt smoke: JPH_VERSION_ID=%llx registered types OK\n",
              (unsigned long long)JPH_VERSION_ID);

  JPH::TempAllocatorImpl temp_allocator(10 * 1024 * 1024);
  JPH::JobSystemSingleThreaded job_system(JPH::cMaxPhysicsJobs);

  BPLayerInterfaceImpl bp_layer_interface;
  ObjectVsBroadPhaseLayerFilterImpl object_vs_bp_filter;
  ObjectLayerPairFilterImpl object_vs_object_filter;

  JPH::PhysicsSystem physics_system;
  physics_system.Init(1024, 0, 1024, 1024, bp_layer_interface, object_vs_bp_filter,
                      object_vs_object_filter);

  JPH::BodyInterface &bi = physics_system.GetBodyInterface();

  JPH::BodyCreationSettings floor_settings(new JPH::BoxShape(JPH::Vec3(100.0f, 1.0f, 100.0f)),
                                           JPH::RVec3(0.0, -1.0, 0.0), JPH::Quat::sIdentity(),
                                           JPH::EMotionType::Static, Layers::NON_MOVING);
  bi.CreateAndAddBody(floor_settings, JPH::EActivation::DontActivate);

  JPH::BodyCreationSettings sphere_settings(new JPH::SphereShape(0.5f), JPH::RVec3(0.0, 2.0, 0.0),
                                            JPH::Quat::sIdentity(), JPH::EMotionType::Dynamic,
                                            Layers::MOVING);
  JPH::BodyID sphere_id = bi.CreateAndAddBody(sphere_settings, JPH::EActivation::Activate);
  bi.SetLinearVelocity(sphere_id, JPH::Vec3(0.0f, -5.0f, 0.0f));

  physics_system.OptimizeBroadPhase();

  const float dt = 1.0f / 60.0f;
  int step = 0;
  while (bi.IsActive(sphere_id) && step < 600) {
    physics_system.Update(dt, 1, &temp_allocator, &job_system);
    ++step;
  }

  JPH::RVec3 pos = bi.GetCenterOfMassPosition(sphere_id);
  const double y = (double)pos.GetY();
  std::printf("jolt smoke: settled after %d steps at y=%.4f (expect ~0.5)\n", step, y);

  bi.RemoveBody(sphere_id);
  bi.DestroyBody(sphere_id);
  JPH::UnregisterTypes();
  delete JPH::Factory::sInstance;
  JPH::Factory::sInstance = nullptr;

  if (step >= 600 || std::fabs(y - 0.5) > 0.05) {
    std::printf("FAIL: sphere did not come to rest on the floor\n");
    return 1;
  }
  std::printf("jolt smoke OK\n");
  return 0;
}
