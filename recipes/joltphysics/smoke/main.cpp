// recipes/joltphysics/smoke/main.cpp — drop spheres on a floor and check they
// come to rest.  Structure follows Jolt's HelloWorld sample.
//
// Always runs the scene on JobSystemSingleThreaded.  When JOLT_SMOKE_THREADS is
// defined (native builds, and the wasm-mt bundle under node) it runs the SAME
// scene again on a JobSystemThreadPool and requires bit-identical final
// positions: the library is built CROSS_PLATFORM_DETERMINISTIC, and Jolt states
// that a step is deterministic with any number of threads given the same API
// calls.  That is the property the physics tier's replay hash rests on, so the
// smoke test checks it rather than assuming it.
//
// The plain wasm bundle is single-threaded: in a non-pthread Emscripten build
// std::thread's constructor throws std::system_error("Not supported") at
// runtime (verified with emsdk 5.0.7 / node 22), so JobSystemThreadPool is only
// exercised where threads exist.
#include <Jolt/Jolt.h>

#include <Jolt/Core/Factory.h>
#include <Jolt/Core/JobSystemSingleThreaded.h>
#include <Jolt/Core/JobSystemThreadPool.h>
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
#include <cstring>
#include <vector>

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

namespace {

// A 4x4 grid of spheres dropped from staggered heights, so the solver has
// several bodies (and, multithreaded, several jobs) per step.
constexpr int kGrid = 4;
constexpr int kMaxSteps = 900;

struct run_result {
  int steps = 0;
  std::vector<double> positions; // x, y, z per sphere
};

run_result run_scene(JPH::JobSystem &job_system) {
  JPH::TempAllocatorImpl temp_allocator(10 * 1024 * 1024);
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

  std::vector<JPH::BodyID> spheres;
  for (int i = 0; i < kGrid; ++i)
    for (int j = 0; j < kGrid; ++j) {
      const double x = 3.0 * (i - 1.5), z = 3.0 * (j - 1.5), y = 2.0 + 0.5 * (i + j);
      JPH::BodyCreationSettings s(new JPH::SphereShape(0.5f), JPH::RVec3(x, y, z),
                                  JPH::Quat::sIdentity(), JPH::EMotionType::Dynamic,
                                  Layers::MOVING);
      JPH::BodyID id = bi.CreateAndAddBody(s, JPH::EActivation::Activate);
      bi.SetLinearVelocity(id, JPH::Vec3(0.0f, -5.0f, 0.0f));
      spheres.push_back(id);
    }
  physics_system.OptimizeBroadPhase();

  const float dt = 1.0f / 60.0f;
  run_result r;
  auto any_active = [&]() {
    for (const JPH::BodyID &id : spheres)
      if (bi.IsActive(id))
        return true;
    return false;
  };
  while (any_active() && r.steps < kMaxSteps) {
    physics_system.Update(dt, 1, &temp_allocator, &job_system);
    ++r.steps;
  }
  for (const JPH::BodyID &id : spheres) {
    const JPH::RVec3 p = bi.GetCenterOfMassPosition(id);
    r.positions.push_back((double)p.GetX());
    r.positions.push_back((double)p.GetY());
    r.positions.push_back((double)p.GetZ());
    bi.RemoveBody(id);
    bi.DestroyBody(id);
  }
  return r;
}

// FNV-1a over the final positions' bytes. Printed so a native and a wasm run can
// be compared: with CROSS_PLATFORM_DETERMINISTIC the two must agree exactly.
unsigned long long state_hash(const run_result &r) {
  unsigned long long h = 1469598103934665603ull;
  const unsigned char *p = reinterpret_cast<const unsigned char *>(r.positions.data());
  for (std::size_t i = 0; i < r.positions.size() * sizeof(double); ++i) {
    h ^= p[i];
    h *= 1099511628211ull;
  }
  return h;
}

bool all_at_rest(const run_result &r) {
  if (r.steps >= kMaxSteps)
    return false;
  for (std::size_t k = 1; k < r.positions.size(); k += 3)
    if (std::fabs(r.positions[k] - 0.5) > 0.05)
      return false;
  return true;
}

} // namespace

int main() {
  JPH::RegisterDefaultAllocator();
  JPH::Factory::sInstance = new JPH::Factory();
  // Aborts the process on a library/consumer JPH_* mismatch (Jolt/RegisterTypes.cpp).
  JPH::RegisterTypes();
  std::printf("jolt smoke: JPH_VERSION_ID=%llx registered types OK\n",
              (unsigned long long)JPH_VERSION_ID);

  JPH::JobSystemSingleThreaded single(JPH::cMaxPhysicsJobs);
  const run_result a = run_scene(single);
  std::printf("jolt smoke: single-threaded: %d spheres settled after %d steps (sphere 0 y=%.4f)\n",
              kGrid * kGrid, a.steps, a.positions[1]);
  std::printf("jolt smoke: state hash %016llx\n", state_hash(a));
  int rc = 0;
  if (!all_at_rest(a)) {
    std::printf("FAIL: spheres did not come to rest on the floor\n");
    rc = 1;
  }

#ifdef JOLT_SMOKE_THREADS
  {
    JPH::JobSystemThreadPool pool(JPH::cMaxPhysicsJobs, JPH::cMaxPhysicsBarriers, 3);
    const run_result b = run_scene(pool);
    const bool same = a.steps == b.steps && a.positions.size() == b.positions.size() &&
                      std::memcmp(a.positions.data(), b.positions.data(),
                                  a.positions.size() * sizeof(double)) == 0;
    std::printf("jolt smoke: thread pool (3 workers): %d steps, %s the single-threaded run\n",
                b.steps, same ? "bit-identical to" : "DIFFERENT from");
    if (!same) {
      std::printf("FAIL: a multithreaded step diverged from the single-threaded one\n");
      rc = 1;
    }
  }
#endif

  JPH::UnregisterTypes();
  delete JPH::Factory::sInstance;
  JPH::Factory::sInstance = nullptr;
  if (rc == 0)
    std::printf("jolt smoke OK\n");
  return rc;
}
